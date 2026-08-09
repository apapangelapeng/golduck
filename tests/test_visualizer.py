from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch

import visualize
import visualizer_eval
from agent_simulation import (
    AgentEvaluationSim,
    AgentSimulationService,
    UnknownAgentSimulationSession,
)
from golduck.errors import ExecutionError
from golduck.rle import encode_rle, parse_rle, pattern_from_cells
from golduck.sim import Sim, Submission, _default_bgolly_path, _sandbox_disabled
from score_batch import ScoreBatchManager
from score_store import ScoreStore
from visualize import app
from visualizer_eval import (
    EvaluationCancelled,
    LiveEvaluationSim,
    evolve_life,
    parse_seed,
    prepare_parallel_outputs,
    secret_for_seed,
)

ACTIVE_SOLUTIONS = visualize._visualizer_solution_names()
ACTIVE_SOLUTION_A, ACTIVE_SOLUTION_B = ACTIVE_SOLUTIONS[:2]


def _live_cells(pattern) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y, intervals in pattern.rows.items()
        for start, end in intervals
        for x in range(start, end)
    }


def _naive_life(
    cells: set[tuple[int, int]],
    width: int,
    height: int,
    generations: int,
) -> set[tuple[int, int]]:
    live = set(cells)
    for _ in range(generations):
        neighbors: dict[tuple[int, int], int] = {}
        for x, y in live:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbor = (x + dx, y + dy)
                    if 0 <= neighbor[0] < width and 0 <= neighbor[1] < height:
                        neighbors[neighbor] = neighbors.get(neighbor, 0) + 1
        live = {
            cell
            for cell, count in neighbors.items()
            if count == 3 or (count == 2 and cell in live)
        }
    return live


class SeedTests(unittest.TestCase):
    def test_parse_seed_accepts_canonical_and_prefixed_hex(self) -> None:
        expected = bytes.fromhex("0123456789abcdef" * 2)
        self.assertEqual(parse_seed("0123456789abcdef" * 2), expected)
        self.assertEqual(parse_seed("0x" + "0123456789abcdef" * 2), expected)

    def test_parse_seed_rejects_wrong_size_and_non_hex(self) -> None:
        for value in ("00", "z" * 32, "0x" + "0" * 31):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_seed(value)

    def test_secret_derivation_matches_production_sim(self) -> None:
        seed = bytes.fromhex("42" * 16)
        sim = Sim(seed)
        for level in (0, 1, 2):
            with self.subTest(level=level):
                self.assertEqual(
                    secret_for_seed(seed, level), sim._get_level(level).secret
                )


class NativeBgollyTests(unittest.TestCase):
    def test_macos_prefers_native_bgolly_from_path(self) -> None:
        with (
            patch("golduck.sim.sys.platform", "darwin"),
            patch("golduck.sim.shutil.which", return_value="/native/bgolly"),
        ):
            self.assertEqual(_default_bgolly_path(), Path("/native/bgolly"))

    def test_linux_keeps_bundled_bgolly(self) -> None:
        with (
            patch("golduck.sim.sys.platform", "linux"),
            patch("golduck.sim.shutil.which") as which,
        ):
            selected = _default_bgolly_path()
        which.assert_not_called()
        self.assertEqual(selected.name, "bgolly")
        self.assertEqual(selected.parent, Path(__file__).resolve().parents[1])

    def test_macos_does_not_attempt_linux_nsjail(self) -> None:
        with (
            patch("golduck.sim.sys.platform", "darwin"),
            patch.dict(os.environ, {"DISABLE_SANDBOX": ""}),
        ):
            self.assertTrue(_sandbox_disabled())


class WasmArtifactTests(unittest.TestCase):
    def test_solution_imports_are_limited_to_the_runner_abi(self) -> None:
        import wasmtime

        root = Path(__file__).resolve().parents[1]
        engine = wasmtime.Engine()
        allowed = {("env", name) for name in ("get_rand", "run", "submit", "finalize")}

        for path in sorted((root / "solution").glob("*.wasm")):
            with self.subTest(solution=path.name):
                module = wasmtime.Module(engine, path.read_bytes())
                imports = {(item.module, item.name) for item in module.imports}
                self.assertTrue(
                    imports <= allowed,
                    f"unsupported imports: {sorted(imports)}",
                )


class ServerStartupTests(unittest.TestCase):
    def test_background_parallelism_reserves_web_capacity(self) -> None:
        expected = {
            1: (1, 1),
            2: (1, 1),
            4: (2, 1),
            6: (2, 2),
            8: (3, 2),
            10: (4, 2),
            14: (4, 3),
            64: (4, 4),
        }
        for logical_cpus, allocation in expected.items():
            with self.subTest(logical_cpus=logical_cpus):
                self.assertEqual(
                    visualize._background_score_parallelism(logical_cpus),
                    allocation,
                )

    def test_server_starts_without_debug_reloader_by_default(self) -> None:
        with (
            patch.dict(os.environ, {"GOLDUCK_DEBUG": ""}),
            patch.object(visualize.app, "run") as run,
        ):
            visualize.main()

        run.assert_called_once_with(
            host="127.0.0.1",
            port=8765,
            debug=False,
            use_reloader=False,
            threaded=True,
        )


class LifeEngineTests(unittest.TestCase):
    def test_blinker_evolves_one_generation(self) -> None:
        pattern = pattern_from_cells({(3, 4), (4, 4), (5, 4)}, 10, 10)
        evolved = evolve_life(pattern, 1)
        self.assertEqual(_live_cells(evolved), {(4, 3), (4, 4), (4, 5)})

    def test_random_patterns_match_naive_life(self) -> None:
        generator = random.Random(0xC0FFEE)
        width = 40
        height = 32
        for case in range(40):
            cells = {
                (x, y)
                for y in range(10, height - 10)
                for x in range(10, width - 10)
                if generator.random() < 0.22
            }
            generations = generator.randrange(10)
            with self.subTest(case=case, generations=generations):
                pattern = pattern_from_cells(cells, width, height)
                evolved = evolve_life(pattern, generations)
                self.assertEqual(
                    _live_cells(evolved),
                    _naive_life(cells, width, height, generations),
                )

    def test_wide_translated_gliders_survive_dynamic_rebasing(self) -> None:
        width = 2_000
        height = 1_000
        right_glider = {(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)}
        left_glider = {(1, 0), (0, 1), (0, 2), (1, 2), (2, 2)}
        cases = ((right_glider, 1_600), (left_glider, 800))
        for shape, generations in cases:
            cells = {(x + 700, y + 100) for x, y in shape}
            with self.subTest(generations=generations):
                pattern = pattern_from_cells(cells, width, height)
                evolved = evolve_life(pattern, generations)
                self.assertEqual(
                    _live_cells(evolved),
                    _naive_life(cells, width, height, generations),
                )

    def test_corner_markers_are_restored_after_evolution(self) -> None:
        width = 80
        height = 80
        corners = {
            (x, y)
            for origin_x, origin_y in (
                (0, 0),
                (width - 2, 0),
                (0, height - 2),
                (width - 2, height - 2),
            )
            for y in (origin_y, origin_y + 1)
            for x in (origin_x, origin_x + 1)
        }
        blinker = {(39, 40), (40, 40), (41, 40)}
        evolved = evolve_life(
            pattern_from_cells(corners | blinker, width, height),
            1,
        )
        self.assertEqual(
            _live_cells(evolved),
            corners | {(40, 39), (40, 40), (40, 41)},
        )

    def test_cancellation_and_deadline_checks_remain_prompt(self) -> None:
        pattern = pattern_from_cells({(3, 4), (4, 4), (5, 4)}, 10, 10)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(EvaluationCancelled):
            evolve_life(pattern, 100, cancelled)
        with self.assertRaises(TimeoutError):
            evolve_life(pattern, 100, deadline=time.monotonic() - 1)


class LifeBackendTests(unittest.TestCase):
    def test_native_backend_matches_portable_backend(self) -> None:
        key = (1, "x = 3, y = 1\n3o!", 4)
        seed = bytes.fromhex("42" * 16)
        try:
            _, native = visualizer_eval._simulate_request_external(seed, key)
        except (ExecutionError, OSError) as exc:
            self.skipTest(f"native bgolly is unavailable: {exc}")
        _, portable = visualizer_eval._simulate_request_portable(seed, key)

        self.assertEqual(
            encode_rle(parse_rle(native, require_header=True)),
            encode_rle(parse_rle(portable, require_header=True)),
        )

    def test_native_backend_is_remembered_after_success(self) -> None:
        key = (1, "pattern", 4)
        with (
            patch.object(visualizer_eval, "_external_bgolly_usable", None),
            patch(
                "visualizer_eval._simulate_request_external",
                return_value=(key, "native"),
            ) as native,
            patch("visualizer_eval._simulate_request_portable") as portable,
        ):
            result = visualizer_eval._simulate_request(bytes(16), key)

            self.assertEqual(result, (key, "native"))
            self.assertTrue(visualizer_eval._external_bgolly_usable)
            native.assert_called_once_with(bytes(16), key)
            portable.assert_not_called()

    def test_failed_native_backend_falls_back_once_per_worker(self) -> None:
        key = (1, "pattern", 4)
        with (
            patch.object(visualizer_eval, "_external_bgolly_usable", None),
            patch(
                "visualizer_eval._simulate_request_external",
                side_effect=ExecutionError("unavailable"),
            ) as native,
            patch(
                "visualizer_eval._simulate_request_portable",
                return_value=(key, "portable"),
            ) as portable,
        ):
            first = visualizer_eval._simulate_request(bytes(16), key)
            second = visualizer_eval._simulate_request(bytes(16), key)

            self.assertEqual(first, (key, "portable"))
            self.assertEqual(second, (key, "portable"))
            self.assertFalse(visualizer_eval._external_bgolly_usable)
            native.assert_called_once_with(bytes(16), key)
            self.assertEqual(portable.call_count, 2)


class ScoreBreakdownTests(unittest.TestCase):
    def test_exact_submission_breakdown_reconciles_to_level_score(self) -> None:
        sim = LiveEvaluationSim(bytes(16), lambda event: None)
        level = sim._get_level(0)
        sim._run_stats[0] = [(55, 1000)]
        sim._submissions[0] = Submission(
            secret=level.secret,
            submission=level.secret,
            known_mask=(1 << 64) - 1,
            guess_mask=0,
        )

        score = sim.level_snapshot(0)
        self.assertTrue(score["exact_answer"])
        self.assertEqual(score["known_weight"], 1.0)
        self.assertEqual(score["exact_bonus"], 100_000.0)
        self.assertAlmostEqual(
            score["performance_score"] + score["exact_bonus"], score["score"]
        )

    def test_selected_level_planning_never_simulates_other_levels(self) -> None:
        calls: list[int] = []

        class InlineExecutor:
            def __init__(self, **_kwargs) -> None:
                pass

            def submit(self, function, *args):
                future: Future = Future()
                try:
                    future.set_result(function(*args))
                except Exception as exc:
                    future.set_exception(exc)
                return future

            def shutdown(self, **_kwargs) -> None:
                pass

        def fake_simulation(seed: bytes, key):
            calls.append(key[0])
            return key, visualizer_eval._empty_canvas_output(key[0])

        requests = [(level, "x = 0, y = 0\n!", 0) for level in (0, 1, 2)]
        with (
            patch("visualizer_eval.ProcessPoolExecutor", InlineExecutor),
            patch(
                "visualizer_eval._planning_pass",
                return_value=(requests, None),
            ),
            patch("visualizer_eval._simulate_request", side_effect=fake_simulation),
        ):
            cache = prepare_parallel_outputs(
                b"planning is mocked",
                bytes(16),
                lambda _event: None,
                parallel_workers=1,
                selected_levels={0},
            )

        self.assertEqual(set(calls), {0})
        self.assertEqual({key[0] for key in cache}, {0, 1, 2})


class AgentSimulationTests(unittest.TestCase):
    EMPTY_RLE = "x = 0, y = 0\n!"

    def setUp(self) -> None:
        self.service = AgentSimulationService()
        started = self.service.handle({"action": "start", "seed": "01" * 16})
        self.session_id = started["session_id"]

    def request(self, action: str, **values):
        return self.service.handle(
            {"action": action, "session_id": self.session_id, **values}
        )

    def test_retains_multi_run_state_and_returns_output_with_score(self) -> None:
        first = self.request(
            "run",
            level=1,
            pattern=self.EMPTY_RLE,
            generations=0,
        )
        second = self.request(
            "run",
            level=1,
            pattern=self.EMPTY_RLE,
            generations=0,
        )
        status = self.request("status")

        self.assertEqual(first["run"], 1)
        self.assertEqual(second["run"], 2)
        self.assertTrue(first["output_rle"].startswith("x = 1000, y = 200"))
        self.assertIsInstance(first["score"], float)
        self.assertEqual(first["score_type"], "performance")
        self.assertLess(second["score"], first["score"])
        self.assertEqual(second["score_breakdown"]["runs_completed"], 2)
        self.assertEqual(second["score_breakdown"]["runs_remaining"], 14)
        self.assertEqual(status["score_breakdown"][0]["runs_completed"], 2)

    def test_lean_path_is_output_and_score_equivalent_to_visualizer(self) -> None:
        seed = bytes.fromhex("23" * 16)
        pattern = b"x = 3, y = 3\nbo$2bo$3o!"
        lean = AgentEvaluationSim(seed)
        reference = LiveEvaluationSim(seed, lambda _event: None)

        lean_output = lean.run(2, pattern, 4)
        reference_output = reference.run(2, pattern, 4)

        self.assertEqual(lean_output, reference_output)
        self.assertEqual(lean._run_stats, reference._run_stats)
        self.assertEqual(lean.level_snapshot(2), reference.level_snapshot(2))

        full_mask = (1 << 64) - 1
        lean.submit(2, 0, 0, full_mask)
        reference.submit(2, 0, 0, full_mask)
        self.assertEqual(lean.level_snapshot(2), reference.level_snapshot(2))

    def test_dense_runs_retain_only_constant_size_metadata(self) -> None:
        dense_rle = "x = 1000, y = 200\n" + "1000o$" * 199 + "1000o!"
        result = self.request(
            "run",
            level=1,
            pattern=dense_rle,
            generations=0,
        )
        session = self.service._sessions[self.session_id]

        self.assertEqual(result["input_cell_count"], 200_000)
        self.assertEqual(
            set(session.simulation.runs[0]),
            {"level", "level_run", "generations", "cell_count"},
        )

    def test_submission_changes_score_to_answer_weighted_score(self) -> None:
        self.request(
            "run",
            level=2,
            rle=self.EMPTY_RLE,
            generations=0,
        )
        submitted = self.request(
            "submit",
            level=2,
            value="0x0",
            known_mask="0x0",
            guess_mask="0x0",
        )
        finalized = self.request("finalize")

        self.assertEqual(submitted["score_type"], "submitted")
        self.assertEqual(submitted["score"], 0.0)
        self.assertIsNone(submitted["score_breakdown"].get("secret"))
        self.assertTrue(finalized["finalized"])
        self.assertEqual(finalized["score"], 0.0)

    def test_never_discloses_secret_and_close_invalidates_session(self) -> None:
        result = self.request(
            "run",
            level=1,
            pattern=self.EMPTY_RLE,
            generations=0,
        )
        self.assertNotIn('"secret"', json.dumps(result))

        closed = self.request("close")
        self.assertTrue(closed["closed"])
        with self.assertRaises(UnknownAgentSimulationSession):
            self.request("status")


class ScoreStoreTests(unittest.TestCase):
    def test_preserves_each_seed_and_tracks_the_latest_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoreStore(Path(directory) / "scores.json")
            first_seed = "00" * 16
            second_seed = "11" * 16

            store.save("future.wasm", first_seed, {"score": 1.0})
            store.save("future.wasm", second_seed, {"score": 2.0})

            self.assertEqual(store.get("future.wasm", first_seed)["score"], 1.0)
            self.assertEqual(store.get("future.wasm", second_seed)["score"], 2.0)
            self.assertEqual(store.latest("future.wasm")["seed"], second_seed)
            self.assertEqual(store.latest_solution(), "future.wasm")
            self.assertEqual(store.seed_order(), [first_seed, second_seed])
            self.assertEqual(
                set(store.all_evaluations()["future.wasm"]),
                {first_seed, second_seed},
            )

            # Updating an existing score must not move its column to the end.
            store.save("future.wasm", first_seed, {"score": 3.0})
            self.assertEqual(store.seed_order(), [first_seed, second_seed])

    def test_migrates_existing_scores_to_first_seen_seed_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scores.json"
            first_seed = "ff" * 16
            second_seed = "00" * 16
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "solutions": {
                            "future.wasm": {
                                "evaluations": {
                                    first_seed: {"updated_at": "2026-01-01T00:00:00Z"},
                                    second_seed: {"updated_at": "2026-01-02T00:00:00Z"},
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = ScoreStore(path)
            self.assertEqual(store.seed_order(), [first_seed, second_seed])

            store.save("future.wasm", second_seed, {"score": 2.0})
            self.assertEqual(
                json.loads(path.read_text())["seed_order"],
                [first_seed, second_seed],
            )


class ScoreBatchManagerTests(unittest.TestCase):
    def test_runs_one_concurrent_pipeline_per_seed(self) -> None:
        seeds = iter(["00" * 16, "11" * 16, "22" * 16])
        first_solution_barrier = threading.Barrier(3)
        calls: list[tuple[str, str]] = []
        calls_lock = threading.Lock()

        def runner(solution: str, seed: str) -> None:
            if solution == "one.wasm":
                first_solution_barrier.wait(timeout=2)
            with calls_lock:
                calls.append((solution, seed))

        manager = ScoreBatchManager(
            runner,
            seed_count=3,
            seed_factory=lambda: next(seeds),
        )
        started, created = manager.start(["one.wasm", "two.wasm"])
        finished = manager.wait(started["id"], timeout=5)

        self.assertTrue(created)
        self.assertEqual(finished["status"], "complete")
        self.assertEqual(finished["completed"], 6)
        self.assertEqual(finished["failures"], [])
        self.assertEqual(
            set(calls),
            {
                (solution, seed)
                for solution in ("one.wasm", "two.wasm")
                for seed in ("00" * 16, "11" * 16, "22" * 16)
            },
        )

    def test_runs_only_explicit_sparse_cells(self) -> None:
        calls: list[tuple[str, str]] = []
        manager = ScoreBatchManager(
            lambda solution, seed: calls.append((solution, seed))
        )
        tasks = [
            ("one.wasm", "00" * 16),
            ("two.wasm", "00" * 16),
            ("two.wasm", "11" * 16),
            ("two.wasm", "11" * 16),
        ]

        started, created = manager.start_tasks(tasks, mode="fill_empty")
        finished = manager.wait(started["id"], timeout=5)

        self.assertTrue(created)
        self.assertEqual(finished["mode"], "fill_empty")
        self.assertEqual(finished["total"], 3)
        self.assertEqual(finished["completed"], 3)
        self.assertEqual(
            set(calls),
            {
                ("one.wasm", "00" * 16),
                ("two.wasm", "00" * 16),
                ("two.wasm", "11" * 16),
            },
        )

    def test_fill_empty_runs_same_seed_cells_in_parallel(self) -> None:
        seed = "22" * 16
        barrier = threading.Barrier(2)
        calls: list[tuple[str, str]] = []
        calls_lock = threading.Lock()

        def runner(solution: str, task_seed: str) -> None:
            barrier.wait(timeout=2)
            with calls_lock:
                calls.append((solution, task_seed))

        manager = ScoreBatchManager(runner)
        started, _ = manager.start_tasks(
            [("one.wasm", seed), ("two.wasm", seed)],
            mode="fill_empty",
        )
        finished = manager.wait(started["id"], timeout=5)

        self.assertEqual(finished["status"], "complete")
        self.assertEqual(finished["failures"], [])
        self.assertEqual(
            set(calls),
            {("one.wasm", seed), ("two.wasm", seed)},
        )

    def test_forwards_selected_level_to_score_runner(self) -> None:
        calls: list[tuple[str, str, int]] = []
        manager = ScoreBatchManager(
            lambda solution, seed, level: calls.append((solution, seed, level))
        )
        seed = "33" * 16

        started, _ = manager.start_tasks(
            [("one.wasm", seed)],
            mode="fill_empty",
            level=0,
        )
        finished = manager.wait(started["id"], timeout=5)

        self.assertEqual(finished["level"], 0)
        self.assertEqual(calls, [("one.wasm", seed, 0)])


class VisualizerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.score_store = ScoreStore(
            Path(self.temporary_directory.name) / "scores.json"
        )
        self.store_patch = patch(
            "visualize.SCORE_STORE",
            self.score_store,
        )
        self.store_patch.start()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.temporary_directory.cleanup()

    def test_level_endpoint_derives_secret_from_seed(self) -> None:
        seed_hex = "ab" * 16
        response = self.client.get(f"/api/level/2?seed={seed_hex}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["seed_hex"], seed_hex)
        self.assertEqual(
            payload["secret_hex"],
            f"0x{secret_for_seed(bytes.fromhex(seed_hex), 2):016x}",
        )

    def test_levels_endpoint_includes_scoring_denominators(self) -> None:
        response = self.client.get("/api/levels")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        scoring = {rule["level"]: rule for rule in payload["scoring"]}

        self.assertEqual(set(scoring), set(payload["levels"]))
        self.assertEqual(payload["active_levels"], [3, 4])
        self.assertEqual(scoring[1]["contestant_width"], 1_000)
        self.assertEqual(scoring[1]["contestant_height"], 200)
        self.assertEqual(scoring[1]["contestant_area"], 1_000 * 200)
        self.assertEqual(scoring[1]["max_generations"], 10_000)
        self.assertEqual(scoring[1]["max_runs"], 16)

    def test_signed_github_push_installs_wasm_for_visualization(self) -> None:
        import wasmtime

        artifact = wasmtime.wat2wasm(
            """(module
              (import "env" "run"
                (func $run (param i32 i32 i32 i32 i32) (result i32)))
              (memory (export "memory") 1)
              (global (export "scratch_ptr") i32 (i32.const 1024))
              (global (export "scratch_cap") i32 (i32.const 64512))
              (data (i32.const 0) "x = 0, y = 0\\0a!\\0a")
              (func (export "run_entry")
                i32.const 3
                i32.const 0
                i32.const 15
                i32.const 0
                i32.const 1024
                call $run
                drop))"""
        )
        repository = "octo/golduck-solutions"
        secret = "test-webhook-secret"
        commit_sha = "a" * 40
        payload = {
            "after": commit_sha,
            "repository": {"full_name": repository},
            "commits": [
                {
                    "added": ["README.md", "build/new_solver.wasm"],
                    "modified": [],
                }
            ],
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(visualize, "SOLUTION_DIR", Path(directory)),
            patch.dict(
                os.environ,
                {
                    visualize.GITHUB_WEBHOOK_SECRET_ENV: secret,
                    visualize.GITHUB_REPOSITORY_ENV: repository,
                    visualize.GITHUB_TOKEN_ENV: "",
                },
            ),
            patch.object(
                visualize,
                "_download_github_wasm",
                return_value=artifact,
            ) as download,
        ):
            response = self.client.post(
                "/api/webhooks/github/wasm",
                data=body,
                content_type="application/json",
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": signature,
                },
            )
            self.assertEqual(response.status_code, 201, response.get_json())
            installed = response.get_json()["installed"][0]
            self.assertEqual(installed["name"], "new_solver.wasm")
            self.assertTrue(installed["updated"])
            self.assertTrue(set(installed["levels"]) & {3, 4})
            self.assertEqual(
                (Path(directory) / "new_solver.wasm").read_bytes(), artifact
            )
            self.assertIn(
                "new_solver.wasm",
                self.client.get("/api/solutions").get_json()["solutions"],
            )
            download.assert_called_once_with(
                "https://api.github.com/repos/octo/golduck-solutions/contents/"
                f"build/new_solver.wasm?ref={commit_sha}"
            )

    def test_github_webhook_requires_configuration_signature_and_repository(
        self,
    ) -> None:
        repository = "octo/golduck-solutions"
        secret = "test-webhook-secret"
        payload = {
            "after": "b" * 40,
            "repository": {"full_name": repository},
            "commits": [],
        }
        body = json.dumps(payload, separators=(",", ":")).encode()

        with patch.dict(
            os.environ,
            {
                visualize.GITHUB_WEBHOOK_SECRET_ENV: "",
                visualize.GITHUB_REPOSITORY_ENV: "",
            },
        ):
            disabled = self.client.post(
                "/api/webhooks/github/wasm",
                data=body,
                content_type="application/json",
            )
        self.assertEqual(disabled.status_code, 503)

        with patch.dict(
            os.environ,
            {
                visualize.GITHUB_WEBHOOK_SECRET_ENV: secret,
                visualize.GITHUB_REPOSITORY_ENV: repository,
            },
        ):
            invalid_signature = self.client.post(
                "/api/webhooks/github/wasm",
                data=body,
                content_type="application/json",
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": "sha256=invalid",
                },
            )
            self.assertEqual(invalid_signature.status_code, 401)

            other_payload = dict(payload)
            other_payload["repository"] = {"full_name": "other/repository"}
            other_body = json.dumps(other_payload, separators=(",", ":")).encode()
            other_signature = (
                "sha256="
                + hmac.new(secret.encode(), other_body, hashlib.sha256).hexdigest()
            )
            wrong_repository = self.client.post(
                "/api/webhooks/github/wasm",
                data=other_body,
                content_type="application/json",
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": other_signature,
                },
            )
            self.assertEqual(wrong_repository.status_code, 403)

    def test_github_release_selects_only_wasm_assets(self) -> None:
        candidates = visualize._github_wasm_candidates(
            "release",
            {
                "action": "published",
                "repository": {"full_name": "octo/golduck-solutions"},
                "release": {
                    "assets": [
                        {
                            "name": "solver.wasm",
                            "url": "https://api.github.com/repos/octo/repo/"
                            "releases/assets/123",
                        },
                        {
                            "name": "notes.txt",
                            "browser_download_url": "https://github.com/notes.txt",
                        },
                    ]
                },
            },
        )
        self.assertEqual(
            candidates,
            [
                {
                    "name": "solver.wasm",
                    "url": "https://api.github.com/repos/octo/repo/releases/assets/123",
                    "source": "release asset solver.wasm",
                }
            ],
        )

    def test_github_webhook_rejects_invalid_wasm_without_installing(self) -> None:
        repository = "octo/golduck-solutions"
        secret = "test-webhook-secret"
        payload = {
            "after": "c" * 40,
            "repository": {"full_name": repository},
            "commits": [{"added": ["bad.wasm"], "modified": []}],
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(visualize, "SOLUTION_DIR", Path(directory)),
            patch.dict(
                os.environ,
                {
                    visualize.GITHUB_WEBHOOK_SECRET_ENV: secret,
                    visualize.GITHUB_REPOSITORY_ENV: repository,
                },
            ),
            patch.object(visualize, "_download_github_wasm", return_value=b"nope"),
        ):
            response = self.client.post(
                "/api/webhooks/github/wasm",
                data=body,
                content_type="application/json",
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": signature,
                },
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_visualizer_defaults_to_level_three(self) -> None:
        response = self.client.get("/static/viz.js")
        try:
            script = response.get_data(as_text=True)
        finally:
            response.close()

        self.assertIn("const DEFAULT_LEVEL = 3;", script)
        self.assertIn("levelSelect.value = String(DEFAULT_LEVEL);", script)

    def test_scoring_calculation_tab_is_served(self) -> None:
        page_response = self.client.get("/")
        script_response = self.client.get("/static/viz.js")
        try:
            page = page_response.get_data(as_text=True)
            script = script_response.get_data(as_text=True)
        finally:
            page_response.close()
            script_response.close()

        self.assertIn('id="tab-scoring"', page)
        self.assertIn('id="scoring-view"', page)
        self.assertIn('id="scoring-calculations"', page)
        self.assertIn("L = [900,000 + R + D + G] × [K + Q] + H", page)
        self.assertIn("maximum 50,000; 0 and 1 run both receive it", page)
        self.assertIn("r = number of completed runs for this level", page)
        self.assertIn("clamp[0, 1](x) = min(1, max(0, x))", page)
        self.assertIn("How density ratio", page)
        self.assertIn("ln(n<sub>i</sub> + 1) ÷ ln(A + 1)", page)
        self.assertIn("after evolution", page)
        self.assertIn("How max generation ratio", page)
        self.assertIn("ln(t<sub>i</sub> + 1) ÷ ln(T<sub>cap</sub> + 1)", page)
        self.assertIn("E = V xor S", page)
        self.assertIn("q<sub>right</sub> = popcount", page)
        self.assertIn("known and guess masks must be disjoint", page)
        self.assertIn("active total = L<sub>3</sub> + L<sub>4</sub>", page)
        self.assertIn("function renderScoringCalculation", script)
        self.assertIn("function buildLevelScoringCalculation", script)
        self.assertIn("function logarithmicScoreRatio", script)
        self.assertIn("Density input Nmax", script)
        self.assertIn("Generation input Tmax", script)
        self.assertIn("Initial live cells", script)
        self.assertIn("Generation ratio", script)
        self.assertIn("Known weight K", script)
        self.assertIn("Guess weight Q", script)

    def test_tabs_persist_in_url_and_comparison_heading_is_flat(self) -> None:
        page_response = self.client.get("/")
        script_response = self.client.get("/static/viz.js")
        tab_state_response = self.client.get("/static/tab-state.js")
        try:
            page = page_response.get_data(as_text=True)
            script = script_response.get_data(as_text=True)
            tab_state = tab_state_response.get_data(as_text=True)
        finally:
            page_response.close()
            script_response.close()
            tab_state_response.close()

        self.assertIn('class="comparison-title"', page)
        self.assertIn('id="tab-comparison"', page)
        self.assertIn('"comparison",\n    "scoring"', script)
        self.assertIn("/static/tab-state.js", page)
        self.assertNotIn("Each row is a Wasm solution", page)
        self.assertIn("function syncTabToUrl", script)
        self.assertIn("window.history.replaceState", script)
        self.assertIn("function urlWithTab", tab_state)

    def test_solution_can_be_selected_from_url(self) -> None:
        response = self.client.get("/static/viz.js")
        try:
            script = response.get_data(as_text=True)
        finally:
            response.close()

        self.assertIn('REQUESTED_PARAMS.get("solution")', script)
        self.assertIn("selected = preferredSolution(names", script)
        self.assertIn("REQUESTED_SOLUTION,", script)

    def test_solution_picker_hot_reloads_new_and_rebuilt_wasm(self) -> None:
        script_response = self.client.get("/static/viz.js")
        idea_lab_response = self.client.get("/static/idea-lab.js")
        try:
            script = script_response.get_data(as_text=True)
            idea_lab = idea_lab_response.get_data(as_text=True)
        finally:
            script_response.close()
            idea_lab_response.close()

        self.assertIn("SOLUTION_POLL_INTERVAL_MS", script)
        self.assertIn("payload.solution_versions", script)
        self.assertIn("loadSolutions({ autoVisualize: true })", script)
        self.assertIn("if (selected) await loadSolution();", script)
        self.assertIn('"golduck:solutions-changed"', script)
        self.assertIn('"golduck:solutions-changed"', idea_lab)
        self.assertIn("loadSolutionChoices(pendingSolutionChoices)", idea_lab)

    def test_agent_simulation_endpoint_supports_stateful_scored_runs(self) -> None:
        started_response = self.client.post(
            "/api/agent/simulate",
            json={"action": "start", "seed": "12" * 16},
        )
        self.assertEqual(started_response.status_code, 200)
        started = started_response.get_json()
        session_id = started["session_id"]

        try:
            first_response = self.client.post(
                "/api/agent/simulate",
                json={
                    "action": "run",
                    "session_id": session_id,
                    "level": 1,
                    "pattern": "x = 0, y = 0\n!",
                    "generations": 0,
                },
            )
            second_response = self.client.post(
                "/api/agent/simulate",
                json={
                    "action": "run",
                    "session_id": session_id,
                    "level": 1,
                    "pattern": "x = 0, y = 0\n!",
                    "generations": 0,
                },
            )
            self.assertEqual(first_response.status_code, 200)
            self.assertEqual(second_response.status_code, 200)
            first = first_response.get_json()
            second = second_response.get_json()
            self.assertEqual((first["run"], second["run"]), (1, 2))
            self.assertIn("output_rle", first)
            self.assertIn("score_breakdown", first)
            self.assertNotIn('"secret"', first_response.get_data(as_text=True))
        finally:
            self.client.post(
                "/api/agent/simulate",
                json={"action": "close", "session_id": session_id},
            )

    def test_agent_simulation_endpoint_rejects_invalid_requests(self) -> None:
        self.assertEqual(self.client.post("/api/agent/simulate").status_code, 400)
        invalid_seed = self.client.post(
            "/api/agent/simulate",
            json={"action": "start", "seed": "not-a-seed"},
        )
        self.assertEqual(invalid_seed.status_code, 400)
        response = self.client.post(
            "/api/agent/simulate",
            json={"action": "status", "session_id": "missing"},
        )
        self.assertEqual(response.status_code, 404)

    def test_secret_preview_tab_uses_the_seed_derived_level_api(self) -> None:
        page_response = self.client.get("/")
        script_response = self.client.get("/static/viz.js")
        try:
            page = page_response.get_data(as_text=True)
            script = script_response.get_data(as_text=True)
        finally:
            page_response.close()
            script_response.close()

        self.assertIn('id="tab-secret"', page)
        self.assertIn('id="secret-board"', page)
        self.assertIn('id="secret-level"', page)
        self.assertIn('id="secret-seed"', page)
        self.assertIn("function loadSecretPreview", script)
        self.assertIn("function drawSecretPreview", script)
        self.assertIn("`/api/level/${level}?seed=${seed}`", script)

    def test_idea_lab_tab_and_editor_module_are_served(self) -> None:
        page_response = self.client.get("/")
        script_response = self.client.get("/static/idea-lab.js")
        engine_response = self.client.get("/static/life-engine.js")
        worker_response = self.client.get("/static/life-worker.js")
        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(engine_response.status_code, 200)
        self.assertEqual(worker_response.status_code, 200)
        try:
            page = page_response.get_data(as_text=True)
            script = script_response.get_data(as_text=True)
            engine = engine_response.get_data(as_text=True)
            worker = worker_response.get_data(as_text=True)
        finally:
            page_response.close()
            script_response.close()
            engine_response.close()
            worker_response.close()

        self.assertIn('id="tab-idea-lab"', page)
        self.assertIn('id="idea-board"', page)
        self.assertIn('id="lab-palette"', page)
        self.assertIn('id="lab-save-json"', page)
        self.assertIn('id="lab-load-json"', page)
        self.assertIn('id="lab-solution"', page)
        self.assertIn('id="lab-solution-run"', page)
        self.assertIn('id="lab-load-solution"', page)
        self.assertIn('id="lab-score-total"', page)
        self.assertIn('id="lab-bulk-enabled"', page)
        self.assertIn('id="lab-bulk-columns"', page)
        self.assertIn('id="lab-bulk-rows"', page)
        self.assertIn('id="lab-tool-pixel"', page)
        self.assertIn('id="lab-sim-step"', page)
        self.assertIn('<option value="500">500 gen/s</option>', page)
        self.assertIn('src="/static/life-engine.js"', page)
        self.assertIn('id: "glider"', script)
        self.assertIn('id: "lwss"', script)
        self.assertIn('id: "mwss"', script)
        self.assertIn('id: "hwss"', script)
        self.assertIn('id: "loafer"', script)
        self.assertIn('id: "copperhead"', script)
        self.assertIn('id: "weekender"', script)
        self.assertIn('id: "crab"', script)
        self.assertIn('id: "canada-goose"', script)
        self.assertIn("function stepLife", script)
        self.assertIn("function projectedExactScore", script)
        self.assertIn("function solutionRunToPlacement", script)
        self.assertIn("function buildStampArray", script)
        self.assertIn("function validateLabState", script)
        self.assertIn("function rasterizeCellLine", script)
        self.assertIn("function runSimulationBatch", script)
        self.assertIn("function stepPackedLife", engine)
        self.assertIn('importScripts("/static/life-engine.js")', worker)

    def test_solution_manifest_does_not_stub_adaptive_runs(self) -> None:
        response = self.client.get(f"/api/solution/{ACTIVE_SOLUTION_A}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["runs"], [])

    def test_solution_manifest_restores_requested_single_level_score(self) -> None:
        root = Path(__file__).resolve().parents[1]
        solution = ACTIVE_SOLUTION_A
        seed = "5b" * 16
        self.score_store.save(
            solution,
            seed,
            {
                "status": "complete",
                "success": True,
                "score": None,
                "evaluated_levels": [2],
                "solution_sha256": hashlib.sha256(
                    (root / "solution" / solution).read_bytes()
                ).hexdigest(),
                "levels": [{"level": 2, "score": 123.0}],
                "runs": [
                    {
                        "level": 2,
                        "level_run": 1,
                        "generations": 500,
                        "cell_count": 3,
                        "rle": "x = 3, y = 1\n3o!",
                        "size": {"w": 3, "h": 1},
                    }
                ],
                "totals": {"score": 123.0, "level_count": 1},
            },
        )

        partial = self.client.get(f"/api/solution/{solution}?seed={seed}&level=2")
        total = self.client.get(f"/api/solution/{solution}?seed={seed}")

        self.assertEqual(partial.status_code, 200)
        payload = partial.get_json()
        self.assertEqual(payload["saved_evaluation"]["seed"], seed)
        self.assertEqual(payload["runs"][0]["level"], 2)
        self.assertEqual(
            payload["runs"][0]["cells"],
            [[-500, -100], [-499, -100], [-498, -100]],
        )
        self.assertEqual(partial.headers["Cache-Control"], "no-store")
        self.assertNotIn("saved_evaluation", total.get_json())

    def test_solution_picker_is_derived_from_the_current_folder(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = []
        for path in (root / "solution").glob("*.wasm"):
            if visualize._solution_levels(path) & visualize.ACTIVE_LEVEL_IDS:
                expected.append(path.name)
        response = self.client.get("/api/solutions")
        page_response = self.client.get("/")
        script_response = self.client.get("/static/viz.js")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["solutions"], sorted(expected))
            self.assertNotIn(
                "DEFAULT_SOLUTION",
                script_response.get_data(as_text=True),
            )
            self.assertNotIn("decode-panel", page_response.get_data(as_text=True))
        finally:
            response.close()
            page_response.close()
            script_response.close()

    def test_solution_inventory_changes_when_wasm_is_added_or_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            solution_directory = Path(directory)
            first = solution_directory / "first.wasm"
            second = solution_directory / "second.wasm"
            first.write_bytes(b"first")

            with (
                patch("visualize.SOLUTION_DIR", solution_directory),
                patch("visualize._solution_levels", return_value=frozenset({3})),
            ):
                initial_response = self.client.get("/api/solutions")
                initial = initial_response.get_json()
                initial_version = initial["solution_versions"][first.name]

                second.write_bytes(b"second artifact")
                added_response = self.client.get("/api/solutions")
                added = added_response.get_json()

                first.write_bytes(b"rebuilt first artifact")
                rebuilt_response = self.client.get("/api/solutions")
                rebuilt = rebuilt_response.get_json()

        self.assertEqual(initial["solutions"], [first.name])
        self.assertEqual(added["solutions"], [first.name, second.name])
        self.assertIn(added["newest_solution"], added["solutions"])
        self.assertNotEqual(
            rebuilt["solution_versions"][first.name],
            initial_version,
        )
        self.assertEqual(initial_response.headers["Cache-Control"], "no-store")

    def test_comparison_row_removal_controls_are_served(self) -> None:
        page_response = self.client.get("/")
        script_response = self.client.get("/static/viz.js")
        try:
            page = page_response.get_data(as_text=True)
            script = script_response.get_data(as_text=True)
        finally:
            page_response.close()
            script_response.close()

        self.assertIn('id="comparison-restore-select"', page)
        self.assertIn('id="comparison-level"', page)
        self.assertNotIn('<option value="0">Level 0</option>', page)
        self.assertNotIn('<option value="1">Level 1</option>', page)
        self.assertNotIn('<option value="2">Level 2</option>', page)
        self.assertIn('<option value="3">Level 3</option>', page)
        self.assertIn('<option value="4">Level 4</option>', page)
        self.assertIn("payload.active_levels", script)
        self.assertIn("function activeEvaluationLevels", script)
        self.assertIn('aria-label="Removed solution to restore"', page)
        self.assertIn("comparison-row-remove", script)
        self.assertIn("excludedComparisonSolutions.delete(solution)", script)
        self.assertIn("solutions: includedSolutions", script)
        self.assertIn("const level = selectedComparisonLevel();", script)
        self.assertIn(
            "await loadSolution(seed || row.latest_seed || null, selectedLevel)",
            script,
        )
        self.assertIn('query.set("level", String(requestedLevel))', script)
        self.assertIn("let solutionLoadSeq = 0;", script)
        self.assertIn("if (seq !== solutionLoadSeq) return;", script)
        self.assertIn('{ cache: "no-store" }', script)

    def test_comparison_endpoint_arranges_scores_and_identifies_winners(self) -> None:
        root = Path(__file__).resolve().parents[1]
        # Deliberately non-lexicographic so the API must preserve insertion order.
        first_seed = "ff" * 16
        second_seed = "11" * 16
        third_seed = "22" * 16
        stale_seed = "33" * 16
        pending_seed = "44" * 16

        def save_score(solution: str, seed: str, score: float) -> None:
            wasm = root / "solution" / solution
            self.score_store.save(
                solution,
                seed,
                {
                    "status": "complete",
                    "success": True,
                    "score": score,
                    "solution_sha256": hashlib.sha256(wasm.read_bytes()).hexdigest(),
                    "levels": [
                        {
                            "level": 3,
                            "score": score * 0.6,
                            "submission": "0x0000000000000001",
                            "known_mask": "0xffffffffffffffff",
                            "guess_mask": "0x0000000000000000",
                        },
                        {
                            "level": 4,
                            "score": score * 0.4,
                            "submission": "0x0000000000000002",
                        },
                    ],
                    "runs": [
                        {"level": 3, "generations": 1_000},
                        {"level": 4, "generations": 2_410},
                        {"level": 4, "generations": 2_000},
                    ],
                },
            )

        save_score(ACTIVE_SOLUTION_A, first_seed, 100.0)
        save_score(ACTIVE_SOLUTION_B, first_seed, 200.0)

        initial_payload = self.client.get("/api/comparison").get_json()
        self.assertEqual(initial_payload["seeds"], [first_seed])

        save_score(ACTIVE_SOLUTION_A, second_seed, 300.0)
        save_score(ACTIVE_SOLUTION_B, second_seed, 300.0)
        save_score(ACTIVE_SOLUTION_A, third_seed, 400.0)
        self.score_store.save(
            ACTIVE_SOLUTION_B,
            pending_seed,
            {
                "status": "running",
                "success": False,
                "score": None,
                "solution_sha256": hashlib.sha256(
                    (root / "solution" / ACTIVE_SOLUTION_B).read_bytes()
                ).hexdigest(),
            },
        )
        self.score_store.save(
            ACTIVE_SOLUTION_B,
            stale_seed,
            {
                "status": "complete",
                "success": True,
                "score": 999.0,
                "solution_sha256": "stale-binary-hash",
            },
        )

        response = self.client.get("/api/comparison")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        rows = {row["solution"]: row for row in payload["solutions"]}

        self.assertEqual(
            payload["seeds"],
            [first_seed, second_seed, third_seed, pending_seed],
        )
        self.assertEqual(rows[ACTIVE_SOLUTION_A]["scores"][first_seed], 100.0)
        self.assertEqual(rows[ACTIVE_SOLUTION_B]["scores"][first_seed], 200.0)
        self.assertNotIn(third_seed, rows[ACTIVE_SOLUTION_B]["scores"])
        self.assertEqual(rows[ACTIVE_SOLUTION_A]["latest_seed"], third_seed)
        self.assertEqual(rows[ACTIVE_SOLUTION_B]["latest_seed"], second_seed)
        self.assertEqual(
            rows[ACTIVE_SOLUTION_A]["workload"],
            {"runs": 3, "total_generations": 5_410},
        )
        self.assertEqual(
            rows[ACTIVE_SOLUTION_A]["level_workloads"],
            [
                {"level": 3, "runs": 1, "max_generations": 1_000},
                {"level": 4, "runs": 2, "max_generations": 2_410},
            ],
        )
        self.assertEqual(
            rows[ACTIVE_SOLUTION_A]["breakdowns"][first_seed],
            [
                {"level": 3, "score": 60.0},
                {"level": 4, "score": 40.0},
            ],
        )
        self.assertEqual(payload["winners"][first_seed], [ACTIVE_SOLUTION_B])
        self.assertEqual(
            payload["winners"][second_seed],
            [ACTIVE_SOLUTION_A, ACTIVE_SOLUTION_B],
        )
        self.assertEqual(payload["winners"][third_seed], [ACTIVE_SOLUTION_A])
        self.assertEqual(payload["winners"][pending_seed], [])
        self.assertEqual(rows[ACTIVE_SOLUTION_A]["wins"], 2)
        self.assertEqual(rows[ACTIVE_SOLUTION_B]["wins"], 2)
        self.assertNotIn(pending_seed, rows[ACTIVE_SOLUTION_B]["scores"])
        self.assertNotIn("workloads", rows[ACTIVE_SOLUTION_B])
        self.assertNotIn(stale_seed, payload["seeds"])

    def test_comparison_endpoint_exposes_partial_level_score_without_total(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        seed = "5a" * 16
        solution = ACTIVE_SOLUTION_A
        self.score_store.save(
            solution,
            seed,
            {
                "status": "complete",
                "success": True,
                "score": None,
                "evaluated_levels": [3],
                "solution_sha256": hashlib.sha256(
                    (root / "solution" / solution).read_bytes()
                ).hexdigest(),
                "levels": [{"level": 3, "score": 123.0}],
                "runs": [{"level": 3, "generations": 1_000}],
            },
        )

        payload = self.client.get("/api/comparison").get_json()
        row = next(row for row in payload["solutions"] if row["solution"] == solution)

        self.assertIn(seed, payload["seeds"])
        self.assertNotIn(seed, row["scores"])
        self.assertEqual(row["breakdowns"][seed], [{"level": 3, "score": 123.0}])

    def test_background_batch_api_starts_and_reports_server_job(self) -> None:
        batch = {
            "id": "batch-1",
            "status": "running",
            "seeds": ["44" * 16],
            "solutions": [ACTIVE_SOLUTION_A, ACTIVE_SOLUTION_B],
            "completed": 0,
            "total": 2,
            "failures": [],
        }
        manager = MagicMock()
        manager.start.return_value = (batch, True)
        manager.latest.return_value = batch

        with patch("visualize.SCORE_BATCH_MANAGER", manager):
            started = self.client.post("/api/comparison/batch")
            status = self.client.get("/api/comparison/batch")

        self.assertEqual(started.status_code, 202)
        self.assertTrue(started.get_json()["created"])
        self.assertEqual(status.get_json()["batch"], batch)
        solutions, _ = manager.start.call_args.args
        self.assertIn(ACTIVE_SOLUTION_A, solutions)
        self.assertIn(ACTIVE_SOLUTION_B, solutions)
        self.assertEqual(
            set(solutions),
            set(self.client.get("/api/solutions").get_json()["solutions"]),
        )

    def test_background_batch_scores_only_requested_comparison_rows(self) -> None:
        batch = {
            "id": "batch-subset",
            "status": "running",
            "seeds": ["77" * 16],
            "solutions": [ACTIVE_SOLUTION_A],
            "completed": 0,
            "total": 1,
            "failures": [],
        }
        manager = MagicMock()
        manager.start.return_value = (batch, True)

        with patch("visualize.SCORE_BATCH_MANAGER", manager):
            response = self.client.post(
                "/api/comparison/batch",
                json={"mode": "random_seeds", "solutions": [ACTIVE_SOLUTION_A]},
            )

        self.assertEqual(response.status_code, 202)
        solutions, _ = manager.start.call_args.args
        self.assertEqual(solutions, [ACTIVE_SOLUTION_A])

    def test_background_batch_forwards_selected_level(self) -> None:
        batch = {
            "id": "batch-level",
            "level": 3,
            "status": "running",
            "seeds": ["78" * 16],
            "solutions": [ACTIVE_SOLUTION_A],
            "completed": 0,
            "total": 1,
            "failures": [],
        }
        manager = MagicMock()
        manager.start.return_value = (batch, True)

        with patch("visualize.SCORE_BATCH_MANAGER", manager):
            response = self.client.post(
                "/api/comparison/batch",
                json={
                    "mode": "random_seeds",
                    "solutions": [ACTIVE_SOLUTION_A],
                    "level": 3,
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(manager.start.call_args.kwargs, {"level": 3})

    def test_background_batch_rejects_unknown_comparison_level(self) -> None:
        response = self.client.post(
            "/api/comparison/batch",
            json={"mode": "random_seeds", "level": 9},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown comparison level", response.get_json()["error"])

    def test_background_batch_rejects_empty_or_unknown_comparison_rows(self) -> None:
        empty = self.client.post(
            "/api/comparison/batch",
            json={"mode": "random_seeds", "solutions": []},
        )
        unknown = self.client.post(
            "/api/comparison/batch",
            json={"mode": "random_seeds", "solutions": ["missing.wasm"]},
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(unknown.status_code, 400)

    def test_fill_empty_batch_schedules_only_missing_table_cells(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seed = "66" * 16
        solution = ACTIVE_SOLUTION_A
        self.score_store.save(
            solution,
            seed,
            {
                "status": "complete",
                "success": True,
                "score": 100.0,
                "solution_sha256": hashlib.sha256(
                    (root / "solution" / solution).read_bytes()
                ).hexdigest(),
                "levels": [],
            },
        )
        available_solutions = set(
            self.client.get("/api/solutions").get_json()["solutions"]
        )
        batch = {
            "id": "batch-fill",
            "mode": "fill_empty",
            "status": "running",
            "seeds": [seed],
            "solutions": sorted(available_solutions - {solution}),
            "completed": 0,
            "total": len(available_solutions) - 1,
            "failures": [],
        }
        manager = MagicMock()
        manager.start_tasks.return_value = (batch, True)

        with patch("visualize.SCORE_BATCH_MANAGER", manager):
            response = self.client.post(
                "/api/comparison/batch",
                json={"mode": "fill_empty"},
            )

        self.assertEqual(response.status_code, 202)
        tasks = manager.start_tasks.call_args.args[0]
        self.assertEqual(
            set(tasks),
            {(name, seed) for name in available_solutions - {solution}},
        )
        self.assertNotIn((solution, seed), tasks)
        self.assertEqual(manager.start_tasks.call_args.kwargs["mode"], "fill_empty")

    def test_fill_empty_batch_excludes_removed_comparison_rows(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seed = "88" * 16
        self.score_store.save(
            ACTIVE_SOLUTION_B,
            seed,
            {
                "status": "complete",
                "success": True,
                "score": 100.0,
                "solution_sha256": hashlib.sha256(
                    (root / "solution" / ACTIVE_SOLUTION_B).read_bytes()
                ).hexdigest(),
                "levels": [],
            },
        )
        manager = MagicMock()
        manager.start_tasks.return_value = (
            {
                "id": "batch-filtered-fill",
                "mode": "fill_empty",
                "status": "running",
                "seeds": [seed],
                "solutions": [ACTIVE_SOLUTION_A],
                "completed": 0,
                "total": 1,
                "failures": [],
            },
            True,
        )

        with patch("visualize.SCORE_BATCH_MANAGER", manager):
            response = self.client.post(
                "/api/comparison/batch",
                json={"mode": "fill_empty", "solutions": [ACTIVE_SOLUTION_A]},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            manager.start_tasks.call_args.args[0],
            [(ACTIVE_SOLUTION_A, seed)],
        )

    def test_fill_empty_batch_only_requires_selected_level(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seed = "89" * 16
        solution = ACTIVE_SOLUTION_A
        self.score_store.save(
            solution,
            seed,
            {
                "status": "complete",
                "success": True,
                "score": None,
                "evaluated_levels": [3],
                "solution_sha256": hashlib.sha256(
                    (root / "solution" / solution).read_bytes()
                ).hexdigest(),
                "levels": [{"level": 3, "score": 100.0}],
                "runs": [],
            },
        )
        manager = MagicMock()
        manager.start_tasks.return_value = (
            {
                "id": "batch-level-fill",
                "mode": "fill_empty",
                "level": 4,
                "status": "running",
                "seeds": [seed],
                "solutions": [solution],
                "completed": 0,
                "total": 1,
                "failures": [],
            },
            True,
        )

        with patch("visualize.SCORE_BATCH_MANAGER", manager):
            already_filled = self.client.post(
                "/api/comparison/batch",
                json={"mode": "fill_empty", "solutions": [solution], "level": 3},
            )
            response = self.client.post(
                "/api/comparison/batch",
                json={"mode": "fill_empty", "solutions": [solution], "level": 4},
            )

        self.assertEqual(already_filled.status_code, 200)
        self.assertIsNone(already_filled.get_json()["batch"])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(manager.start_tasks.call_count, 1)
        self.assertEqual(manager.start_tasks.call_args.args[0], [(solution, seed)])
        self.assertEqual(
            manager.start_tasks.call_args.kwargs,
            {"mode": "fill_empty", "level": 4},
        )

    def test_level_background_evaluation_merges_without_other_levels(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seed = "5b" * 16
        solution = ACTIVE_SOLUTION_A
        solution_sha256 = hashlib.sha256(
            (root / "solution" / solution).read_bytes()
        ).hexdigest()
        self.score_store.save(
            solution,
            seed,
            {
                "status": "complete",
                "success": True,
                "score": None,
                "evaluated_levels": [0],
                "solution_sha256": solution_sha256,
                "levels": [
                    {
                        "level": 0,
                        "score": 100.0,
                        "potential_score": 100.0,
                        "submitted": True,
                    }
                ],
                "runs": [{"level": 0, "generations": 1_000}],
                "submissions": [],
            },
        )

        def fake_evaluation(
            path,
            seed_bytes,
            emit,
            *,
            parallel_workers=None,
            selected_levels=None,
        ):
            self.assertEqual(parallel_workers, 1)
            self.assertEqual(selected_levels, {2})
            emit({"type": "start", "seed": seed_bytes.hex(), "levels": []})
            emit(
                {
                    "type": "complete",
                    "success": True,
                    "score": 300.0,
                    "levels": [
                        {
                            "level": 2,
                            "score": 300.0,
                            "potential_score": 300.0,
                            "submitted": True,
                        }
                    ],
                    "runs": [{"level": 2, "generations": 4_000}],
                    "submissions": [],
                    "totals": {"score": 300.0},
                }
            )

        with patch("visualize.evaluate_solution", side_effect=fake_evaluation):
            visualize._score_in_background(solution, seed, 2)

        record = self.score_store.get(solution, seed)
        self.assertEqual(record["evaluated_levels"], [0, 2])
        self.assertIsNone(record["score"])
        self.assertEqual(
            {entry["level"]: entry["score"] for entry in record["levels"]},
            {0: 100.0, 2: 300.0},
        )
        self.assertEqual({run["level"] for run in record["runs"]}, {0, 2})

    def test_failed_total_generation_preserves_partial_level_scores(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seed = "5c" * 16
        solution = ACTIVE_SOLUTION_A
        original = {
            "status": "complete",
            "success": True,
            "score": None,
            "evaluated_levels": [0],
            "solution_sha256": hashlib.sha256(
                (root / "solution" / solution).read_bytes()
            ).hexdigest(),
            "levels": [{"level": 0, "score": 100.0}],
            "runs": [],
        }
        self.score_store.save(solution, seed, original)

        def failing_evaluation(path, seed_bytes, emit, *, parallel_workers=None):
            emit({"type": "start", "seed": seed_bytes.hex(), "levels": []})
            raise RuntimeError("planned failure")

        with (
            patch("visualize.evaluate_solution", side_effect=failing_evaluation),
            self.assertRaisesRegex(RuntimeError, "planned failure"),
        ):
            visualize._score_in_background(solution, seed)

        record = self.score_store.get(solution, seed)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["evaluated_levels"], [0])
        self.assertEqual(record["levels"], original["levels"])

    def test_background_evaluation_persists_without_stream_client(self) -> None:
        seed = "55" * 16

        def fake_evaluation(path, seed_bytes, emit, *, parallel_workers=None):
            self.assertEqual(parallel_workers, 1)
            emit({"type": "start", "seed": seed_bytes.hex(), "levels": []})
            emit(
                {
                    "type": "complete",
                    "success": True,
                    "score": 456.0,
                    "levels": [{"level": 0, "score": 456.0}],
                    "runs": [],
                    "totals": {"score": 456.0},
                }
            )

        with patch("visualize.evaluate_solution", side_effect=fake_evaluation):
            visualize._score_in_background(ACTIVE_SOLUTION_A, seed)

        record = self.score_store.get(ACTIVE_SOLUTION_A, seed)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["score"], 456.0)

    def test_evaluation_endpoint_streams_events(self) -> None:
        parallel_worker_counts = []

        def fake_evaluation(path, seed, emit, cancelled, *, parallel_workers=None):
            parallel_worker_counts.append(parallel_workers)
            emit({"type": "start", "seed": seed.hex(), "levels": []})
            emit(
                {
                    "type": "complete",
                    "success": True,
                    "score": 123.0,
                    "levels": [],
                    "runs": [],
                    "totals": {"score": 123.0},
                }
            )

        with patch(
            "visualize.evaluate_solution", side_effect=fake_evaluation
        ) as evaluator:
            response = self.client.get(
                f"/api/evaluate/{ACTIVE_SOLUTION_A}?seed="
                + "00" * 16
                + "&seed_batch=1",
                buffered=True,
            )
            cached_response = self.client.get(
                f"/api/evaluate/{ACTIVE_SOLUTION_A}?seed=" + "00" * 16,
                buffered=True,
            )

        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.data.splitlines()]
        self.assertEqual([event["type"] for event in events], ["start", "complete"])
        self.assertEqual(events[-1]["score"], 123.0)
        self.assertEqual(evaluator.call_count, 1)
        self.assertEqual(parallel_worker_counts, [1])
        cached_events = [json.loads(line) for line in cached_response.data.splitlines()]
        self.assertTrue(cached_events[-1]["cached"])

        manifest = self.client.get(f"/api/solution/{ACTIVE_SOLUTION_A}").get_json()
        self.assertEqual(manifest["latest_seed"], "00" * 16)
        self.assertEqual(manifest["saved_evaluation"]["totals"]["score"], 123.0)


if __name__ == "__main__":
    unittest.main()
