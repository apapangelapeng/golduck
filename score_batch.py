"""Server-owned background batches for cross-seed score generation."""

from __future__ import annotations

import secrets
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

ScoreRunner = Callable[..., None]
SeedFactory = Callable[[], str]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ScoreBatchManager:
    """Run one background batch at a time, one concurrent pipeline per seed."""

    def __init__(
        self,
        runner: ScoreRunner,
        *,
        seed_count: int = 10,
        seed_factory: SeedFactory | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if seed_count < 1:
            raise ValueError("seed_count must be at least 1")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.runner = runner
        self.seed_count = seed_count
        self.seed_factory = seed_factory or (lambda: secrets.token_hex(16))
        self.max_concurrency = max_concurrency
        self._lock = threading.RLock()
        self._batches: dict[str, dict[str, Any]] = {}
        self._work: dict[str, dict[str, list[str]]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._active_id: str | None = None
        self._latest_id: str | None = None

    def start(
        self,
        solutions: list[str],
        existing_seeds: set[str] | None = None,
        *,
        level: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if not solutions:
            raise ValueError("at least one solution is required")
        with self._lock:
            active = self._active_locked()
            if active is not None:
                return active, False

            used = set(existing_seeds or ())
            seeds: list[str] = []
            while len(seeds) < self.seed_count:
                seed = self.seed_factory().lower()
                if seed in used:
                    continue
                used.add(seed)
                seeds.append(seed)

            return self._start_locked(
                [(solution, seed) for seed in seeds for solution in solutions],
                mode="random_seeds",
                level=level,
            )

    def start_tasks(
        self,
        tasks: list[tuple[str, str]],
        *,
        mode: str = "fill_empty",
        level: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Start a sparse batch of explicit solution/seed cells."""
        with self._lock:
            active = self._active_locked()
            if active is not None:
                return active, False
            return self._start_locked(tasks, mode=mode, level=level)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_id is None:
                return None
            state = self._batches.get(self._latest_id)
            return deepcopy(state) if state is not None else None

    def wait(
        self, batch_id: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            thread = self._threads.get(batch_id)
        if thread is not None:
            thread.join(timeout)
        with self._lock:
            state = self._batches.get(batch_id)
            return deepcopy(state) if state is not None else None

    def _run(self, batch_id: str) -> None:
        with self._lock:
            work = deepcopy(self._work[batch_id])
            mode = self._batches[batch_id].get("mode")
            level = self._batches[batch_id].get("level")

        if mode == "fill_empty":
            groups = [
                [(solution, seed)]
                for seed, solutions in work.items()
                for solution in solutions
            ]
            thread_name_prefix = f"golduck-cell-{batch_id[:8]}"
        else:
            groups = [
                [(solution, seed) for solution in solutions]
                for seed, solutions in work.items()
            ]
            thread_name_prefix = f"golduck-seed-{batch_id[:8]}"

        with ThreadPoolExecutor(
            max_workers=min(len(groups), self.max_concurrency),
            thread_name_prefix=thread_name_prefix,
        ) as executor:
            futures = [
                executor.submit(self._run_group, batch_id, group, level)
                for group in groups
            ]
            for future in as_completed(futures):
                future.result()

        with self._lock:
            state = self._batches[batch_id]
            state["status"] = (
                "complete_with_errors" if state["failures"] else "complete"
            )
            state["finished_at"] = _now()
            if self._active_id == batch_id:
                self._active_id = None
            self._work.pop(batch_id, None)

    def _run_group(
        self,
        batch_id: str,
        tasks: list[tuple[str, str]],
        level: int | None,
    ) -> None:
        for solution, seed in tasks:
            try:
                if level is None:
                    self.runner(solution, seed)
                else:
                    self.runner(solution, seed, level)
            except Exception as exc:
                message = str(exc).replace("\n", " ").replace("\r", " ").strip()
                with self._lock:
                    self._batches[batch_id]["failures"].append(
                        {
                            "solution": solution,
                            "seed": seed,
                            "message": message[:500] or type(exc).__name__,
                        }
                    )
            finally:
                with self._lock:
                    self._batches[batch_id]["completed"] += 1

    def _active_locked(self) -> dict[str, Any] | None:
        if self._active_id is None:
            return None
        active = self._batches.get(self._active_id)
        if active is None or active.get("status") != "running":
            return None
        return deepcopy(active)

    def _start_locked(
        self,
        tasks: list[tuple[str, str]],
        *,
        mode: str,
        level: int | None,
    ) -> tuple[dict[str, Any], bool]:
        work: dict[str, list[str]] = {}
        solutions: list[str] = []
        seen_solutions: set[str] = set()
        seen_tasks: set[tuple[str, str]] = set()
        for solution, seed in tasks:
            if not solution or not seed:
                raise ValueError("score tasks require a solution and seed")
            seed = seed.lower()
            task = (solution, seed)
            if task in seen_tasks:
                continue
            seen_tasks.add(task)
            work.setdefault(seed, []).append(solution)
            if solution not in seen_solutions:
                seen_solutions.add(solution)
                solutions.append(solution)

        if not seen_tasks:
            raise ValueError("at least one score task is required")

        batch_id = uuid.uuid4().hex
        state: dict[str, Any] = {
            "id": batch_id,
            "mode": mode,
            "level": level,
            "status": "running",
            "seeds": list(work),
            "solutions": solutions,
            "completed": 0,
            "total": len(seen_tasks),
            "failures": [],
            "started_at": _now(),
            "finished_at": None,
        }
        self._batches[batch_id] = state
        self._work[batch_id] = work
        self._active_id = batch_id
        self._latest_id = batch_id
        thread = threading.Thread(
            target=self._run,
            args=(batch_id,),
            name=f"golduck-score-batch-{batch_id[:8]}",
            daemon=True,
        )
        self._threads[batch_id] = thread
        thread.start()
        return deepcopy(state), True
