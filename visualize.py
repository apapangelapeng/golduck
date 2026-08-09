"""Local level visualizer for golduck."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import multiprocessing
import os
import queue
import re
import tempfile
import threading
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_from_directory,
    stream_with_context,
)

import runner_core
from agent_simulation import (
    AgentSimulationError,
    UnknownAgentSimulationSession,
    agent_simulate_and_score,
)
from golduck.errors import ChallengeError
from golduck.levels import LEVELS
from golduck.rle import parse_rle
from score_batch import ScoreBatchManager
from score_store import ScoreStore
from visualizer_eval import (
    EvaluationCancelled,
    _planning_pass,
    evaluate_solution,
    parse_seed,
    secret_for_seed,
)

ROOT = Path(__file__).resolve().parent
VIZ_DIR = ROOT / "viz"
SOLUTION_DIR = ROOT / "solution"
SCORE_STORE = ScoreStore(ROOT / "score_history.json")
ALL_LEVEL_IDS = frozenset(LEVELS)
DEPRECATED_LEVEL_IDS = frozenset({0, 1, 2})
ACTIVE_LEVEL_IDS = ALL_LEVEL_IDS.difference(DEPRECATED_LEVEL_IDS)
GITHUB_WEBHOOK_SECRET_ENV = "GOLDUCK_GITHUB_WEBHOOK_SECRET"
GITHUB_REPOSITORY_ENV = "GOLDUCK_GITHUB_REPOSITORY"
GITHUB_TOKEN_ENV = "GOLDUCK_GITHUB_TOKEN"
GITHUB_WEBHOOK_MAX_ARTIFACTS = 20
GITHUB_DOWNLOAD_HOSTS = frozenset(
    {"api.github.com", "github.com", "raw.githubusercontent.com"}
)
GITHUB_INSTALL_LOCK = threading.Lock()

app = Flask(__name__, static_folder=None)


def _background_score_parallelism(
    logical_cpus: int | None,
) -> tuple[int, int]:
    """Allocate refill pipelines and Life workers while reserving web capacity."""
    cpu_count = max(1, logical_cpus or 1)
    reserved = 2 if cpu_count >= 4 else int(cpu_count >= 2)
    worker_budget = max(1, cpu_count - reserved)
    candidates = [
        (pipelines, workers)
        for pipelines in range(1, 5)
        for workers in range(1, 5)
        if pipelines * workers <= worker_budget
    ]
    return max(
        candidates,
        key=lambda choice: (
            choice[0] * choice[1],
            min(choice),
            choice[0],
        ),
    )


SCORE_PIPELINES, SCORE_SIMULATION_WORKERS = _background_score_parallelism(
    os.cpu_count()
)


def _rect(name: str, values: tuple[int, int, int, int]) -> dict[str, object]:
    x, y, w, h = values
    return {"name": name, "x": x, "y": y, "w": w, "h": h}


def _cells_from_rle(rle: str, origin_x: int, origin_y: int) -> list[list[int]]:
    pattern = parse_rle(rle)
    cells: list[list[int]] = []
    for row_y, intervals in pattern.rows.items():
        for start, end in intervals:
            for x in range(start, end):
                cells.append([origin_x + x, origin_y + row_y])
    return cells


def _solution_path(name: str) -> Path | None:
    path = SOLUTION_DIR / name
    if path.parent != SOLUTION_DIR or path.suffix != ".wasm" or not path.is_file():
        return None
    return path


def _solution_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=256)
def _traced_solution_levels(
    path_text: str,
    modified_ns: int,
    size: int,
) -> frozenset[int]:
    """Trace a Wasm's inert planning path and return every requested level."""
    del modified_ns, size  # They are cache-busting parts of the key.
    path = Path(path_text)
    pending, error = _planning_pass(path.read_bytes(), bytes(16), {})
    levels = frozenset(level for level, _, _ in pending if level in LEVELS)
    if not levels and error is not None:
        raise error
    return levels


def _solution_levels(path: Path) -> frozenset[int]:
    stat = path.stat()
    return _traced_solution_levels(str(path), stat.st_mtime_ns, stat.st_size)


def _visualizer_solution_names() -> list[str]:
    """List only solutions that invoke at least one supported active level."""
    names: list[str] = []
    for path in SOLUTION_DIR.glob("*.wasm"):
        try:
            levels = _solution_levels(path)
        except Exception as exc:
            # An invalid or untraceable artifact cannot be established as an
            # active Level 3/4 solution, so keep it out of every picker.
            app.logger.warning("Skipping untraceable solution %s: %s", path.name, exc)
            continue
        if levels & ACTIVE_LEVEL_IDS:
            names.append(path.name)
    return sorted(names)


class GitHubWebhookError(ValueError):
    """A GitHub delivery or downloaded artifact is unsafe or unusable."""


def _github_wasm_filename(value: object) -> str | None:
    if not isinstance(value, str) or "\\" in value:
        return None
    name = value.rsplit("/", 1)[-1]
    if not name.lower().endswith(".wasm"):
        return None
    normalized = name[:-5] + ".wasm"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.wasm", normalized):
        raise GitHubWebhookError(f"unsafe Wasm filename {name!r}")
    return normalized


def _github_repository_name(payload: dict[str, object]) -> str | None:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return None
    full_name = repository.get("full_name")
    return full_name if isinstance(full_name, str) else None


def _github_wasm_candidates(
    event: str,
    payload: dict[str, object],
) -> list[dict[str, str]]:
    repository = _github_repository_name(payload)
    if repository is None or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise GitHubWebhookError("delivery has no valid GitHub repository")

    candidates: dict[str, dict[str, str]] = {}
    if event == "push":
        commit_sha = payload.get("after")
        if not isinstance(commit_sha, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40,64}", commit_sha
        ):
            raise GitHubWebhookError("push delivery has no valid commit SHA")
        commits = payload.get("commits")
        if not isinstance(commits, list):
            raise GitHubWebhookError("push delivery has no commit list")
        paths: set[str] = set()
        for commit in commits:
            if not isinstance(commit, dict):
                continue
            for field in ("added", "modified"):
                changed = commit.get(field)
                if isinstance(changed, list):
                    paths.update(path for path in changed if isinstance(path, str))
        owner, repo = repository.split("/", 1)
        for path in sorted(paths):
            name = _github_wasm_filename(path)
            if name is None:
                continue
            path_parts = path.split("/")
            if path.startswith("/") or any(
                part in {"", ".", ".."} for part in path_parts
            ):
                raise GitHubWebhookError(f"unsafe repository path {path!r}")
            contents_url = (
                "https://api.github.com/repos/"
                f"{quote(owner, safe='')}/{quote(repo, safe='')}/contents/"
                f"{quote(path, safe='/')}?ref={quote(commit_sha, safe='')}"
            )
            candidates[name] = {"name": name, "url": contents_url, "source": path}

    elif event == "release":
        action = payload.get("action")
        if action not in {"published", "released", "edited"}:
            return []
        release = payload.get("release")
        assets = release.get("assets") if isinstance(release, dict) else None
        if not isinstance(assets, list):
            raise GitHubWebhookError("release delivery has no asset list")
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = _github_wasm_filename(asset.get("name"))
            if name is None:
                continue
            download_url = asset.get("url") or asset.get("browser_download_url")
            if not isinstance(download_url, str):
                raise GitHubWebhookError(f"release asset {name!r} has no download URL")
            candidates[name] = {
                "name": name,
                "url": download_url,
                "source": f"release asset {asset.get('name')}",
            }

    if len(candidates) > GITHUB_WEBHOOK_MAX_ARTIFACTS:
        raise GitHubWebhookError(
            f"delivery contains more than {GITHUB_WEBHOOK_MAX_ARTIFACTS} Wasm files"
        )
    return list(candidates.values())


def _validate_github_download_url(url: str) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise GitHubWebhookError(
            "artifact URL is not an approved GitHub HTTPS URL"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in GITHUB_DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise GitHubWebhookError("artifact URL is not an approved GitHub HTTPS URL")


def _download_github_wasm(url: str) -> bytes:
    _validate_github_download_url(url)
    headers = {
        "Accept": (
            "application/vnd.github.raw+json"
            if "/contents/" in urlsplit(url).path
            else "application/octet-stream"
        ),
        "User-Agent": "golduck-visualizer-webhook",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(UrlRequest(url, headers=headers), timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > runner_core.MAX_WASM_BYTES:
                raise GitHubWebhookError("GitHub Wasm artifact is too large")
            data = response.read(runner_core.MAX_WASM_BYTES + 1)
    except GitHubWebhookError:
        raise
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise GitHubWebhookError(f"GitHub artifact download failed: {exc}") from exc
    if len(data) > runner_core.MAX_WASM_BYTES:
        raise GitHubWebhookError("GitHub Wasm artifact is too large")
    return data


def _stage_github_wasm(
    name: str,
    data: bytes,
) -> tuple[Path, frozenset[int], str]:
    if not data.startswith(b"\x00asm"):
        raise GitHubWebhookError(f"{name} is not a WebAssembly binary")
    try:
        import wasmtime

        module = wasmtime.Module(wasmtime.Engine(), data)
    except Exception as exc:
        raise GitHubWebhookError(f"{name} is not valid WebAssembly: {exc}") from exc
    allowed_imports = {
        ("env", import_name)
        for import_name in ("get_rand", "run", "submit", "finalize")
    }
    imports = {(item.module, item.name) for item in module.imports}
    unsupported = imports.difference(allowed_imports)
    if unsupported:
        raise GitHubWebhookError(
            f"{name} has unsupported imports: {sorted(unsupported)}"
        )

    SOLUTION_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".github-wasm-",
        suffix=".tmp",
        dir=SOLUTION_DIR,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        levels = _solution_levels(temporary_path)
        if not levels & ACTIVE_LEVEL_IDS:
            raise GitHubWebhookError(
                f"{name} does not invoke an active Level 3 or Level 4 run"
            )
        temporary_path.chmod(0o644)
        return temporary_path, levels, hashlib.sha256(data).hexdigest()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _install_staged_github_wasm(
    name: str,
    data: bytes,
    temporary_path: Path,
    levels: frozenset[int],
    sha256: str,
) -> dict[str, object]:
    destination = SOLUTION_DIR / name
    if destination.exists() and destination.read_bytes() == data:
        temporary_path.unlink(missing_ok=True)
        updated = False
    else:
        os.replace(temporary_path, destination)
        updated = True
        _traced_solution_levels.cache_clear()
    return {
        "name": name,
        "sha256": sha256,
        "levels": sorted(levels),
        "updated": updated,
        "visualizer_url": f"/?solution={quote(name, safe='')}",
    }


def _solution_inventory(
    names: list[str],
) -> tuple[list[str], dict[str, str], str | None]:
    """Return stable file versions and the freshest currently visible solution."""
    available: list[str] = []
    versions: dict[str, str] = {}
    newest: tuple[int, str] | None = None
    for name in names:
        path = _solution_path(name)
        if path is None:
            continue
        try:
            stat = path.stat()
        except OSError:
            # A build can replace a Wasm between the directory scan and this
            # stat. The next browser poll will pick up the completed artifact.
            continue
        available.append(name)
        versions[name] = f"{stat.st_mtime_ns}:{stat.st_size}:{stat.st_ino}"
        freshness = (max(stat.st_mtime_ns, stat.st_ctime_ns), name)
        if newest is None or freshness > newest:
            newest = freshness
    return available, versions, newest[1] if newest is not None else None


def _compact_run(run: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in run.items() if key != "cells"}


def _hydrate_run(run: dict[str, object]) -> dict[str, object]:
    hydrated = deepcopy(run)
    if "cells" in hydrated:
        return hydrated
    level_id = hydrated.get("level")
    rle = hydrated.get("rle")
    if isinstance(level_id, int) and level_id in LEVELS and isinstance(rle, str):
        origin_x, origin_y, _, _ = LEVELS[level_id](0).get_contestant_rect()
        hydrated["cells"] = _cells_from_rle(rle, origin_x, origin_y)
    else:
        hydrated["cells"] = []
    return hydrated


def _hydrate_record(record: dict[str, Any]) -> dict[str, Any]:
    hydrated = deepcopy(record)
    hydrated["runs"] = [
        _hydrate_run(run) for run in hydrated.get("runs", []) if isinstance(run, dict)
    ]
    hydrated["cached"] = True
    return hydrated


def _set_level(levels: list[dict[str, object]], value: dict[str, object]) -> None:
    level_id = value.get("level")
    for index, current in enumerate(levels):
        if current.get("level") == level_id:
            levels[index] = deepcopy(value)
            return
    levels.append(deepcopy(value))
    levels.sort(key=lambda item: int(item.get("level", 0)))


def _apply_score_event(record: dict[str, Any], event: dict[str, object]) -> bool:
    """Update a persisted snapshot and report whether it should be flushed."""
    event_type = event.get("type")
    if event_type == "start":
        record["status"] = "running"
        record["levels"] = deepcopy(event.get("levels", []))
        record["totals"] = deepcopy(event.get("totals", {}))
        return True
    if event_type == "run_complete":
        level_score = event.get("level_score")
        run = event.get("run")
        if isinstance(level_score, dict):
            _set_level(record["levels"], level_score)
        if isinstance(run, dict):
            record["runs"].append(_compact_run(run))
        record["totals"] = deepcopy(event.get("totals", record["totals"]))
        return True
    if event_type == "submission":
        level_score = event.get("level_score")
        submission = event.get("submission")
        if isinstance(level_score, dict):
            _set_level(record["levels"], level_score)
        if isinstance(submission, dict):
            record["submissions"].append(deepcopy(submission))
        record["totals"] = deepcopy(event.get("totals", record["totals"]))
        return True
    if event_type == "complete":
        record.update(
            {
                "status": "complete",
                "success": bool(event.get("success")),
                "score": event.get("score"),
                "levels": deepcopy(event.get("levels", [])),
                "totals": deepcopy(event.get("totals", {})),
                "runs": [
                    _compact_run(run)
                    for run in event.get("runs", [])
                    if isinstance(run, dict)
                ],
                "submissions": deepcopy(event.get("submissions", [])),
                "detail": deepcopy(event.get("detail", {})),
                "call_counts": deepcopy(event.get("call_counts", {})),
            }
        )
        return True
    if event_type == "error":
        record["status"] = "error"
        record["error"] = event.get("error")
        return True
    return False


def _explicit_evaluated_levels(record: dict[str, Any]) -> set[int] | None:
    value = record.get("evaluated_levels")
    if not isinstance(value, list):
        return None
    return {
        level
        for level in value
        if isinstance(level, int) and not isinstance(level, bool) and level in LEVELS
    }


def _record_level_score(record: dict[str, Any], level: int) -> float | None:
    if record.get("status") != "complete" or record.get("success") is not True:
        return None
    evaluated = _explicit_evaluated_levels(record)
    if evaluated is not None and level not in evaluated:
        return None
    levels = record.get("levels")
    if not isinstance(levels, list):
        return None
    for entry in levels:
        if not isinstance(entry, dict) or entry.get("level") != level:
            continue
        score = entry.get("score")
        if (
            not isinstance(score, bool)
            and isinstance(score, (int, float))
            and math.isfinite(score)
        ):
            return float(score)
    return None


def _record_has_total_score(record: dict[str, Any]) -> bool:
    score = record.get("score")
    if (
        record.get("status") != "complete"
        or record.get("success") is not True
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        return False
    evaluated = _explicit_evaluated_levels(record)
    # Records written before level-scoped generation were always full runs.
    return evaluated is None or ALL_LEVEL_IDS.issubset(evaluated)


def _new_score_record(
    solution_sha256: str,
    evaluated_levels: set[int] | frozenset[int] | None = None,
) -> dict[str, Any]:
    selected = ALL_LEVEL_IDS if evaluated_levels is None else evaluated_levels
    return {
        "solution_sha256": solution_sha256,
        "evaluated_levels": sorted(selected),
        "status": "starting",
        "success": False,
        "score": None,
        "levels": [],
        "totals": {},
        "runs": [],
        "submissions": [],
        "detail": {},
        "call_counts": {},
    }


def _merge_level_score_record(
    existing: dict[str, Any] | None,
    partial: dict[str, Any],
    level: int,
) -> dict[str, Any]:
    """Replace one level in a saved cell without discarding other level scores."""
    solution_sha256 = str(partial["solution_sha256"])
    if (
        existing is not None
        and existing.get("solution_sha256") == solution_sha256
        and existing.get("status") == "complete"
        and existing.get("success") is True
    ):
        merged = deepcopy(existing)
        evaluated = _explicit_evaluated_levels(existing)
        if evaluated is None:
            evaluated = {
                candidate
                for candidate in LEVELS
                if _record_level_score(existing, candidate) is not None
            }
    else:
        merged = _new_score_record(solution_sha256, set())
        evaluated = set()

    def replace_level_entries(key: str) -> None:
        current = merged.get(key)
        replacement = partial.get(key)
        kept = (
            [
                deepcopy(entry)
                for entry in current
                if isinstance(current, list)
                and isinstance(entry, dict)
                and entry.get("level") != level
            ]
            if isinstance(current, list)
            else []
        )
        added = (
            [
                deepcopy(entry)
                for entry in replacement
                if isinstance(replacement, list)
                and isinstance(entry, dict)
                and entry.get("level") == level
            ]
            if isinstance(replacement, list)
            else []
        )
        merged[key] = kept + added

    for key in ("levels", "runs", "submissions"):
        replace_level_entries(key)

    evaluated.add(level)
    merged.update(
        {
            "solution_sha256": solution_sha256,
            "evaluated_levels": sorted(evaluated),
            "status": "complete",
            "success": True,
            "error": None,
            "call_counts": deepcopy(partial.get("call_counts", {})),
        }
    )

    level_scores = {
        candidate: _record_level_score(merged, candidate) for candidate in LEVELS
    }
    has_total = ALL_LEVEL_IDS.issubset(evaluated) and all(
        score is not None for score in level_scores.values()
    )
    merged["score"] = (
        sum(float(score) for score in level_scores.values() if score is not None)
        if has_total
        else None
    )
    known_levels = [
        entry
        for entry in merged.get("levels", [])
        if isinstance(entry, dict) and entry.get("level") in evaluated
    ]
    merged["totals"] = {
        "score": sum(
            float(entry["score"])
            for entry in known_levels
            if not isinstance(entry.get("score"), bool)
            and isinstance(entry.get("score"), (int, float))
            and math.isfinite(entry["score"])
        ),
        "potential_score": sum(
            float(entry["potential_score"])
            for entry in known_levels
            if not isinstance(entry.get("potential_score"), bool)
            and isinstance(entry.get("potential_score"), (int, float))
            and math.isfinite(entry["potential_score"])
        ),
        "submitted_levels": sum(bool(entry.get("submitted")) for entry in known_levels),
        "level_count": len(evaluated),
    }
    merged["detail"] = deepcopy(partial.get("detail", {}))
    return merged


def _isolated_evaluation_worker(
    path_text: str,
    seed: bytes,
    parallel_workers: int | None,
    selected_levels: tuple[int, ...] | None,
    sender: Any,
) -> None:
    """Evaluate in a process whose Python lock is independent of Flask."""
    try:
        result = evaluate_solution(
            Path(path_text),
            seed,
            lambda _event: None,
            parallel_workers=parallel_workers,
            selected_levels=selected_levels,
        )
        sender.send({"ok": True, "result": result})
    except BaseException as exc:
        message = str(exc).replace("\n", " ").replace("\r", " ").strip()
        sender.send(
            {
                "ok": False,
                "error": message[:500] or type(exc).__name__,
            }
        )
    finally:
        sender.close()


def _evaluate_solution_isolated(
    path: Path,
    seed: bytes,
    emit: Any,
    *,
    parallel_workers: int | None = None,
    selected_levels: set[int] | frozenset[int] | None = None,
) -> dict[str, object]:
    """Run a complete background score outside the web server process."""
    emit({"type": "start", "seed": seed.hex(), "levels": [], "totals": {}})
    simulation_workers = max(parallel_workers or 1, SCORE_SIMULATION_WORKERS)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_evaluation_worker,
        args=(
            str(path),
            seed,
            simulation_workers,
            tuple(sorted(selected_levels)) if selected_levels is not None else None,
            sender,
        ),
        name=f"golduck-score-{seed.hex()[:8]}",
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(360.0):
            process.terminate()
            process.join(5.0)
            raise TimeoutError("background score evaluation timed out")
        try:
            payload = receiver.recv()
        except EOFError as exc:
            raise RuntimeError(
                "background score worker exited without a result"
            ) from exc
    finally:
        receiver.close()

    process.join(10.0)
    if process.is_alive():
        process.terminate()
        process.join(5.0)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(str(error or "background score evaluation failed"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("background score worker returned an invalid result")
    emit({"type": "complete", **result})
    return result


def _score_in_background_with_evaluator(
    solution: str,
    seed_hex: str,
    level: int | None,
    evaluator: Any,
) -> None:
    """Evaluate and persist a score without depending on an HTTP connection."""
    path = _solution_path(solution)
    if path is None:
        raise ValueError(f"unknown solution {solution!r}")
    solution_sha256 = _solution_sha256(path)
    if level is not None and level not in LEVELS:
        raise ValueError(f"unknown comparison level {level!r}")
    cached = SCORE_STORE.get(solution, seed_hex)
    if (
        cached is not None
        and cached.get("solution_sha256") == solution_sha256
        and (
            _record_has_total_score(cached)
            if level is None
            else _record_level_score(cached, level) is not None
        )
    ):
        return

    seed = parse_seed(seed_hex)
    persisted = _new_score_record(
        solution_sha256,
        None if level is None else {level},
    )
    preserve_cached_partial = (
        cached is not None
        and cached.get("solution_sha256") == solution_sha256
        and cached.get("status") == "complete"
        and cached.get("success") is True
        and not _record_has_total_score(cached)
    )

    def emit(event: dict[str, object]) -> None:
        should_persist = _apply_score_event(persisted, event)
        event_type = event.get("type")
        if (
            level is None
            and should_persist
            and event_type in {"start", "complete", "error"}
            and (event_type == "complete" or not preserve_cached_partial)
        ):
            SCORE_STORE.save(solution, seed_hex, persisted)
        elif level is not None and should_persist and event_type == "complete":
            SCORE_STORE.save(
                solution,
                seed_hex,
                _merge_level_score_record(cached, persisted, level),
            )

    try:
        if level is None:
            evaluator(path, seed, emit, parallel_workers=1)
        else:
            evaluator(
                path,
                seed,
                emit,
                parallel_workers=1,
                selected_levels={level},
            )
    except Exception as exc:
        message = str(exc).replace("\n", " ").replace("\r", " ").strip()
        emit(
            {
                "type": "error",
                "error": message[:500] or type(exc).__name__,
            }
        )
        raise


def _score_in_background(
    solution: str,
    seed_hex: str,
    level: int | None = None,
) -> None:
    """Synchronous entry point retained for direct callers."""
    _score_in_background_with_evaluator(
        solution,
        seed_hex,
        level,
        evaluate_solution,
    )


def _score_in_background_isolated(
    solution: str,
    seed_hex: str,
    level: int | None = None,
) -> None:
    _score_in_background_with_evaluator(
        solution,
        seed_hex,
        level,
        _evaluate_solution_isolated,
    )


# Keep the CPU-aware number of isolated score pipelines active. Process
# isolation protects Flask from Wasmtime's Python lock, while the small caps
# bound process and file-descriptor use on a sparse refill.
SCORE_BATCH_MANAGER = ScoreBatchManager(
    _score_in_background_isolated,
    max_concurrency=SCORE_PIPELINES,
)


def _focus_bounds(
    rects: list[tuple[int, int, int, int]],
    cells: list[list[int]],
) -> dict[str, int]:
    xs: list[int] = []
    ys: list[int] = []
    for x, y, w, h in rects:
        xs.extend([x, x + w])
        ys.extend([y, y + h])
    for x, y in cells:
        xs.append(x)
        ys.append(y)
    if not xs or not ys:
        return {"x": -50, "y": -50, "w": 100, "h": 100}
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = max(40, int(0.08 * max(max_x - min_x, max_y - min_y, 1)))
    return {
        "x": min_x - pad,
        "y": min_y - pad,
        "w": max_x - min_x + 2 * pad,
        "h": max_y - min_y + 2 * pad,
    }


@app.get("/")
def index():
    return send_from_directory(VIZ_DIR, "index.html")


@app.get("/static/<path:filename>")
def viz_static(filename: str):
    return send_from_directory(VIZ_DIR, filename)


@app.get("/api/levels")
def list_levels():
    level_ids = sorted(LEVELS)
    scoring: list[dict[str, object]] = []
    for level_id in level_ids:
        level = LEVELS[level_id](0)
        _, _, contestant_width, contestant_height = level.get_contestant_rect()
        min_generations, max_generations = level.get_generation_range()
        scoring.append(
            {
                "level": level_id,
                "max_runs": level.get_max_runs(),
                "contestant_width": contestant_width,
                "contestant_height": contestant_height,
                "contestant_area": contestant_width * contestant_height,
                "min_generations": min_generations,
                "max_generations": max_generations,
            }
        )
    return jsonify(
        {
            "levels": level_ids,
            "active_levels": sorted(ACTIVE_LEVEL_IDS),
            "scoring": scoring,
        }
    )


@app.post("/api/agent/simulate")
def agent_simulation_api():
    """Expose the stateful simulation/scoring tool over JSON."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    try:
        result = agent_simulate_and_score(payload)
    except UnknownAgentSimulationSession as exc:
        return jsonify({"error": str(exc)}), 404
    except TimeoutError as exc:
        return jsonify({"error": str(exc)}), 408
    except (AgentSimulationError, ChallengeError) as exc:
        return jsonify({"error": str(exc)}), 400
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/level/<int:level_id>")
def get_level(level_id: int):
    if level_id not in LEVELS:
        return jsonify({"error": f"unknown level {level_id}"}), 404

    seed_hex = None
    seed_raw = request.args.get("seed")
    if seed_raw is not None:
        try:
            seed = parse_seed(seed_raw)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        seed_hex = seed.hex()
        secret = secret_for_seed(seed, level_id)
    else:
        # Keep the original direct-secret query available for bookmarked
        # visualizer URLs and focused level-design work.
        secret_raw = request.args.get("secret", "1")
        try:
            secret = int(secret_raw, 0)
        except ValueError:
            return jsonify(
                {"error": "secret must be an integer (decimal or 0x hex)"}
            ), 400
        if secret < 0 or secret >= 1 << 64:
            return jsonify({"error": "secret must be a 64-bit unsigned integer"}), 400

    level = LEVELS[level_id](secret)
    secret_x, secret_y, secret_rle = level.get_secret()
    canvas = level.get_canvas_rect()
    contestant = level.get_contestant_rect()
    viewing = level.get_viewing_rect()
    min_gen, max_gen = level.get_generation_range()
    cells = _cells_from_rle(secret_rle, secret_x, secret_y)
    focus_rects = [contestant, viewing, (secret_x, secret_y, 1, 1)]
    pattern = parse_rle(secret_rle)
    focus_rects.append((secret_x, secret_y, pattern.width, pattern.height))

    return jsonify(
        {
            "level": level_id,
            "seed_hex": seed_hex,
            "secret": secret,
            "secret_hex": f"0x{secret:016x}",
            "secret_rle": secret_rle,
            "secret_origin": {"x": secret_x, "y": secret_y},
            "secret_size": {"w": pattern.width, "h": pattern.height},
            "cells": cells,
            "cell_count": len(cells),
            "rects": [
                _rect("canvas", canvas),
                _rect("secret", (secret_x, secret_y, pattern.width, pattern.height)),
                _rect("contestant", contestant),
                _rect("viewing", viewing),
            ],
            "focus": _focus_bounds(focus_rects, cells),
            "generations": {"min": min_gen, "max": max_gen},
            "max_runs": level.get_max_runs(),
        }
    )


@app.get("/api/solutions")
def list_solutions():
    names, versions, newest = _solution_inventory(_visualizer_solution_names())
    latest = SCORE_STORE.latest_solution()
    response = jsonify(
        {
            "solutions": names,
            "latest_solution": latest if latest in names else None,
            "newest_solution": newest,
            "solution_versions": versions,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/webhooks/github/wasm")
def github_wasm_webhook():
    secret = os.environ.get(GITHUB_WEBHOOK_SECRET_ENV, "").strip()
    configured_repository = os.environ.get(GITHUB_REPOSITORY_ENV, "").strip()
    if not secret or not configured_repository:
        return jsonify({"error": "GitHub Wasm webhook is not configured"}), 503

    body = request.get_data(cache=True)
    supplied_signature = request.headers.get("X-Hub-Signature-256", "")
    expected_signature = (
        "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return jsonify({"error": "invalid GitHub webhook signature"}), 401

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({"error": "GitHub webhook body must be JSON"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "GitHub webhook body must be a JSON object"}), 400

    repository = _github_repository_name(payload)
    if repository is None or repository.casefold() != configured_repository.casefold():
        return jsonify({"error": "GitHub webhook repository is not allowed"}), 403

    event = request.headers.get("X-GitHub-Event", "").strip().lower()
    if event == "ping":
        return jsonify({"event": event, "repository": repository, "status": "ready"})
    try:
        candidates = _github_wasm_candidates(event, payload)
        downloaded = [
            (candidate, _download_github_wasm(candidate["url"]))
            for candidate in candidates
        ]
        staged: list[tuple[dict[str, str], bytes, Path, frozenset[int], str]] = []
        with GITHUB_INSTALL_LOCK:
            try:
                for candidate, data in downloaded:
                    temporary_path, levels, sha256 = _stage_github_wasm(
                        candidate["name"], data
                    )
                    staged.append((candidate, data, temporary_path, levels, sha256))
                installed = [
                    _install_staged_github_wasm(
                        candidate["name"], data, temporary_path, levels, sha256
                    )
                    for candidate, data, temporary_path, levels, sha256 in staged
                ]
            finally:
                for _, _, temporary_path, _, _ in staged:
                    temporary_path.unlink(missing_ok=True)
    except GitHubWebhookError as exc:
        app.logger.warning("Rejected GitHub Wasm webhook: %s", exc)
        return jsonify({"error": str(exc)}), 422

    status_code = 201 if any(item["updated"] for item in installed) else 200
    if not candidates:
        status_code = 202
    return (
        jsonify(
            {
                "event": event,
                "repository": repository,
                "installed": installed,
                "status": "installed" if installed else "no Wasm artifacts",
            }
        ),
        status_code,
    )


def _comparison_payload() -> dict[str, object]:
    """Build current-binary scores arranged by solution and seed."""
    names = _visualizer_solution_names()
    stored = SCORE_STORE.all_evaluations()
    rows: list[dict[str, Any]] = []
    seeds: set[str] = set()

    for name in names:
        path = _solution_path(name)
        if path is None:
            continue
        solution_sha256 = _solution_sha256(path)
        scores: dict[str, float] = {}
        breakdowns: dict[str, list[dict[str, object]]] = {}
        workloads_by_seed: dict[str, dict[str, int]] = {}
        level_workloads_by_seed: dict[str, list[dict[str, int]]] = {}
        latest_seed: str | None = None
        latest_updated_at = ""
        for seed, evaluation in stored.get(name, {}).items():
            if evaluation.get("solution_sha256") != solution_sha256:
                continue
            # Put a generated seed on the board as soon as its evaluation is
            # persisted. Its cell stays empty until a valid score completes.
            seeds.add(seed)
            if (
                evaluation.get("status") != "complete"
                or evaluation.get("success") is not True
            ):
                continue
            if _record_has_total_score(evaluation):
                scores[seed] = float(evaluation["score"])
            runs = evaluation.get("runs")
            level_workloads: dict[int, dict[str, int]] = {}
            valid_runs = False
            if isinstance(runs, list):
                total_generations = 0
                valid_runs = True
                for run in runs:
                    if not isinstance(run, dict):
                        valid_runs = False
                        break
                    generations = run.get("generations")
                    if (
                        isinstance(generations, bool)
                        or not isinstance(generations, int)
                        or generations < 0
                    ):
                        valid_runs = False
                        break
                    total_generations += generations
                    level_id = run.get("level")
                    if isinstance(level_id, bool) or not isinstance(level_id, int):
                        valid_runs = False
                        break
                    level_workload = level_workloads.setdefault(
                        level_id,
                        {"runs": 0, "max_generations": 0},
                    )
                    level_workload["runs"] += 1
                    level_workload["max_generations"] = max(
                        level_workload["max_generations"], generations
                    )
                if valid_runs:
                    workloads_by_seed[seed] = {
                        "runs": len(runs),
                        "total_generations": total_generations,
                    }
                else:
                    level_workloads = {}
            level_scores: list[dict[str, object]] = []
            levels = evaluation.get("levels")
            if isinstance(levels, list):
                for level in levels:
                    if not isinstance(level, dict):
                        continue
                    level_id = level.get("level")
                    level_score = level.get("score")
                    if isinstance(level_id, bool) or not isinstance(level_id, int):
                        continue
                    if (
                        isinstance(level_score, bool)
                        or not isinstance(level_score, (int, float))
                        or not math.isfinite(level_score)
                    ):
                        level_score = None
                    level_detail: dict[str, object] = {
                        "level": level_id,
                        "score": (
                            float(level_score) if level_score is not None else None
                        ),
                    }
                    if isinstance(runs, list) and valid_runs:
                        level_workloads.setdefault(
                            level_id,
                            {"runs": 0, "max_generations": 0},
                        )
                    level_scores.append(level_detail)
            if isinstance(runs, list) and valid_runs:
                level_workloads_by_seed[seed] = [
                    {"level": level_id, **level_workload}
                    for level_id, level_workload in sorted(level_workloads.items())
                ]
            breakdowns[seed] = sorted(level_scores, key=lambda item: int(item["level"]))
            updated_at = evaluation.get("updated_at")
            updated_at = updated_at if isinstance(updated_at, str) else ""
            if latest_seed is None or updated_at > latest_updated_at:
                latest_seed = seed
                latest_updated_at = updated_at
        workload_seed = (
            latest_seed
            if latest_seed in workloads_by_seed
            else next(iter(workloads_by_seed), None)
        )
        rows.append(
            {
                "solution": name,
                "latest_seed": latest_seed,
                "scores": scores,
                "breakdowns": breakdowns,
                "workload": (
                    workloads_by_seed.get(workload_seed)
                    if workload_seed is not None
                    else None
                ),
                "level_workloads": (
                    level_workloads_by_seed.get(workload_seed, [])
                    if workload_seed is not None
                    else []
                ),
            }
        )

    persisted_order = SCORE_STORE.seed_order()
    ordered_seeds = [seed for seed in persisted_order if seed in seeds]
    ordered_seed_set = set(ordered_seeds)
    ordered_seeds.extend(sorted(seeds - ordered_seed_set))
    winners: dict[str, list[str]] = {}
    for seed in ordered_seeds:
        candidates = [
            (row["solution"], row["scores"][seed])
            for row in rows
            if seed in row["scores"]
        ]
        if not candidates:
            winners[seed] = []
            continue
        highest = max(score for _, score in candidates)
        winners[seed] = [name for name, score in candidates if score == highest]

    for row in rows:
        solution = row["solution"]
        row["wins"] = sum(solution in seed_winners for seed_winners in winners.values())

    return {"seeds": ordered_seeds, "solutions": rows, "winners": winners}


def _missing_comparison_tasks(
    payload: dict[str, object],
    included_solutions: list[str] | None = None,
    level: int | None = None,
) -> list[tuple[str, str]]:
    seeds = payload.get("seeds")
    rows = payload.get("solutions")
    if not isinstance(seeds, list) or not isinstance(rows, list):
        return []

    included = set(included_solutions) if included_solutions is not None else None
    tasks: list[tuple[str, str]] = []
    for seed in seeds:
        if not isinstance(seed, str):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            solution = row.get("solution")
            scores = row.get("scores")
            breakdowns = row.get("breakdowns")
            has_score = isinstance(scores, dict) and seed in scores
            if level is not None and isinstance(breakdowns, dict):
                levels = breakdowns.get(seed)
                has_score = (
                    any(
                        isinstance(entry, dict)
                        and entry.get("level") == level
                        and not isinstance(entry.get("score"), bool)
                        and isinstance(entry.get("score"), (int, float))
                        and math.isfinite(entry["score"])
                        for entry in levels
                    )
                    if isinstance(levels, list)
                    else False
                )
            if (
                isinstance(solution, str)
                and (included is None or solution in included)
                and not has_score
            ):
                tasks.append((solution, seed))
    return tasks


def _requested_comparison_level(request_payload: object) -> int | None:
    if not isinstance(request_payload, dict) or request_payload.get("level") is None:
        return None
    level = request_payload.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or level not in LEVELS:
        raise ValueError(f"unknown comparison level {level!r}")
    return level


def _requested_comparison_solutions(request_payload: object) -> list[str]:
    available = _visualizer_solution_names()
    if not isinstance(request_payload, dict) or "solutions" not in request_payload:
        return available

    requested = request_payload.get("solutions")
    if not isinstance(requested, list):
        raise ValueError("solutions must be a list of available Wasm filenames")

    available_set = set(available)
    names: list[str] = []
    seen: set[str] = set()
    for name in requested:
        if not isinstance(name, str) or name not in available_set:
            raise ValueError(f"unknown comparison solution {name!r}")
        if name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        raise ValueError("at least one comparison solution must be included")
    return names


@app.get("/api/comparison")
def get_comparison():
    """Return current-binary scores arranged by solution and seed."""
    response = jsonify(_comparison_payload())
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/comparison/batch")
def get_comparison_batch():
    response = jsonify({"batch": SCORE_BATCH_MANAGER.latest()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/comparison/batch")
def start_comparison_batch():
    request_payload = request.get_json(silent=True)
    mode = (
        request_payload.get("mode")
        if isinstance(request_payload, dict)
        else "random_seeds"
    )
    if mode not in {None, "random_seeds", "fill_empty"}:
        return jsonify({"error": f"unknown comparison batch mode {mode!r}"}), 400
    try:
        names = _requested_comparison_solutions(request_payload)
        level = _requested_comparison_level(request_payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if mode == "fill_empty":
        tasks = _missing_comparison_tasks(_comparison_payload(), names, level)
        if not tasks:
            scope = "total" if level is None else f"Level {level}"
            response = jsonify(
                {
                    "batch": None,
                    "created": False,
                    "message": (
                        f"All included {scope} score cells are already filled."
                    ),
                }
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        try:
            if level is None:
                batch, created = SCORE_BATCH_MANAGER.start_tasks(
                    tasks,
                    mode="fill_empty",
                )
            else:
                batch, created = SCORE_BATCH_MANAGER.start_tasks(
                    tasks,
                    mode="fill_empty",
                    level=level,
                )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        response = jsonify({"batch": batch, "created": created})
        response.status_code = 202 if created else 200
        response.headers["Cache-Control"] = "no-store"
        return response

    existing = {
        seed
        for evaluations in SCORE_STORE.all_evaluations().values()
        for seed in evaluations
    }
    try:
        if level is None:
            batch, created = SCORE_BATCH_MANAGER.start(names, existing)
        else:
            batch, created = SCORE_BATCH_MANAGER.start(
                names,
                existing,
                level=level,
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    response = jsonify({"batch": batch, "created": created})
    response.status_code = 202 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/solution/<name>")
def get_solution(name: str):
    path = _solution_path(name)
    if path is None:
        return jsonify({"error": f"unknown solution {name!r}"}), 404

    requested_level: int | None = None
    level_raw = request.args.get("level")
    if level_raw is not None:
        try:
            requested_level = int(level_raw, 10)
        except ValueError:
            return jsonify({"error": f"unknown level {level_raw!r}"}), 400
        if requested_level not in LEVELS:
            return jsonify({"error": f"unknown level {requested_level}"}), 400

    payload: dict[str, object] = {"solution": name, "runs": []}
    latest = SCORE_STORE.latest(name)
    if latest is not None:
        payload["latest_seed"] = latest["seed"]

    selected = latest
    seed_raw = request.args.get("seed")
    if seed_raw is not None:
        try:
            seed_hex = parse_seed(seed_raw).hex()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        selected = SCORE_STORE.get(name, seed_hex)
        if selected is not None:
            selected["seed"] = seed_hex

    if selected is not None and selected.get("solution_sha256") == _solution_sha256(
        path
    ):
        has_requested_score = (
            _record_has_total_score(selected)
            if requested_level is None
            else _record_level_score(selected, requested_level) is not None
        )
        if has_requested_score:
            restored = _hydrate_record(selected)
            payload["runs"] = restored.get("runs", [])
            payload["saved_evaluation"] = {
                "seed": restored["seed"],
                "status": "complete",
                "levels": restored.get("levels", []),
                "totals": restored.get("totals", {}),
                "call_counts": restored.get("call_counts", {}),
                "cached": True,
            }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/evaluate/<name>")
def evaluate(name: str):
    """Stream exact per-run and per-level score updates as NDJSON."""
    path = _solution_path(name)
    if path is None:
        return jsonify({"error": f"unknown solution {name!r}"}), 404
    try:
        seed = parse_seed(request.args.get("seed", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    seed_hex = seed.hex()
    solution_sha256 = _solution_sha256(path)
    seed_batch = request.args.get("seed_batch") == "1"

    cached = SCORE_STORE.get(name, seed_hex)
    if (
        cached is not None
        and cached.get("solution_sha256") == solution_sha256
        and _record_has_total_score(cached)
    ):
        # Accessing a cached result also makes this the selection restored on
        # the next page load.
        SCORE_STORE.save(name, seed_hex, cached)
        restored = _hydrate_record(cached)

        @stream_with_context
        def cached_stream():
            yield (
                json.dumps(
                    {
                        "type": "start",
                        "seed": seed_hex,
                        "levels": restored.get("levels", []),
                        "totals": restored.get("totals", {}),
                        "cached": True,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            yield (
                json.dumps(
                    {
                        "type": "complete",
                        "success": restored.get("success", True),
                        "score": restored.get("score"),
                        "seed": seed_hex,
                        "levels": restored.get("levels", []),
                        "totals": restored.get("totals", {}),
                        "runs": restored.get("runs", []),
                        "submissions": restored.get("submissions", []),
                        "detail": restored.get("detail", {}),
                        "call_counts": restored.get("call_counts", {}),
                        "cached": True,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

        return Response(
            cached_stream(),
            content_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    events: queue.Queue[dict[str, object] | None] = queue.Queue()
    cancelled = threading.Event()
    persisted = _new_score_record(solution_sha256)
    preserve_cached_partial = (
        cached is not None
        and cached.get("solution_sha256") == solution_sha256
        and cached.get("status") == "complete"
        and cached.get("success") is True
        and not _record_has_total_score(cached)
    )

    def emit(event: dict[str, object]) -> None:
        if cancelled.is_set():
            raise EvaluationCancelled("evaluation cancelled")
        should_persist = _apply_score_event(persisted, event)
        event_type = event.get("type")
        batch_snapshot = event_type in {"start", "complete", "error"}
        if (
            should_persist
            and (not seed_batch or batch_snapshot)
            and (not preserve_cached_partial or event_type == "complete")
        ):
            SCORE_STORE.save(name, seed_hex, persisted)
        events.put(event)

    def worker() -> None:
        try:
            if seed_batch:
                evaluate_solution(
                    path,
                    seed,
                    emit,
                    cancelled,
                    parallel_workers=1,
                )
            else:
                evaluate_solution(path, seed, emit, cancelled)
        except EvaluationCancelled:
            pass
        except Exception as exc:
            message = str(exc).replace("\n", " ").replace("\r", " ").strip()
            error_event = {
                "type": "error",
                "error": message[:500] or type(exc).__name__,
            }
            try:
                emit(error_event)
            except EvaluationCancelled:
                pass
        finally:
            events.put(None)

    threading.Thread(target=worker, name="golduck-evaluation", daemon=True).start()

    @stream_with_context
    def stream():
        try:
            while True:
                event = events.get()
                if event is None:
                    break
                yield json.dumps(event, separators=(",", ":")) + "\n"
        finally:
            cancelled.set()

    return Response(
        stream(),
        content_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    debug = os.environ.get("GOLDUCK_DEBUG", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.run(
        host="127.0.0.1",
        port=8765,
        debug=debug,
        use_reloader=debug,
        threaded=True,
    )


if __name__ == "__main__":
    main()
