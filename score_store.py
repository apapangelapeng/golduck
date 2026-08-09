"""Atomic JSON persistence for visualizer score evaluations."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STORE_VERSION = 1
_LOCK = threading.RLock()


class ScoreStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _empty(self) -> dict[str, object]:
        return {"version": STORE_VERSION, "seed_order": [], "solutions": {}}

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(value, dict) or value.get("version") != STORE_VERSION:
            return self._empty()
        if not isinstance(value.get("solutions"), dict):
            value["solutions"] = {}
        seed_order = value.get("seed_order")
        needs_seed_order_migration = not isinstance(seed_order, list)
        if needs_seed_order_migration:
            seed_order = []

        # Score files created before seed_order existed still need a stable
        # board order. Recover it from the oldest available timestamps once;
        # subsequent saves preserve that order explicitly.
        seen: set[str] = set()
        normalized_order: list[str] = []
        for seed in seed_order:
            if isinstance(seed, str) and seed not in seen:
                normalized_order.append(seed)
                seen.add(seed)

        if not needs_seed_order_migration:
            value["seed_order"] = normalized_order
            return value

        legacy_seeds: dict[str, str] = {}
        for solution_entry in value["solutions"].values():
            if not isinstance(solution_entry, dict):
                continue
            evaluations = solution_entry.get("evaluations")
            if not isinstance(evaluations, dict):
                continue
            for seed, evaluation in evaluations.items():
                if not isinstance(seed, str) or seed in seen:
                    continue
                timestamp = ""
                if isinstance(evaluation, dict):
                    created_at = evaluation.get("created_at")
                    updated_at = evaluation.get("updated_at")
                    if isinstance(created_at, str):
                        timestamp = created_at
                    elif isinstance(updated_at, str):
                        timestamp = updated_at
                previous = legacy_seeds.get(seed)
                if previous is None or (timestamp and timestamp < previous):
                    legacy_seeds[seed] = timestamp
        normalized_order.extend(
            sorted(legacy_seeds, key=lambda seed: (legacy_seeds[seed], seed))
        )
        value["seed_order"] = normalized_order
        return value

    def _write_unlocked(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def get(self, solution: str, seed: str) -> dict[str, Any] | None:
        with _LOCK:
            document = self._load_unlocked()
            solutions = document["solutions"]
            solution_entry = solutions.get(solution)
            if not isinstance(solution_entry, dict):
                return None
            evaluations = solution_entry.get("evaluations")
            if not isinstance(evaluations, dict):
                return None
            value = evaluations.get(seed)
            return deepcopy(value) if isinstance(value, dict) else None

    def latest(self, solution: str) -> dict[str, Any] | None:
        with _LOCK:
            document = self._load_unlocked()
            solutions = document["solutions"]
            solution_entry = solutions.get(solution)
            if not isinstance(solution_entry, dict):
                return None
            seed = solution_entry.get("latest_seed")
            evaluations = solution_entry.get("evaluations")
            if not isinstance(seed, str) or not isinstance(evaluations, dict):
                return None
            value = evaluations.get(seed)
            if not isinstance(value, dict):
                return None
            result = deepcopy(value)
            result["seed"] = seed
            return result

    def latest_solution(self) -> str | None:
        with _LOCK:
            document = self._load_unlocked()
            value = document.get("latest_solution")
            return value if isinstance(value, str) else None

    def all_evaluations(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return a detached solution -> seed -> evaluation snapshot."""
        with _LOCK:
            document = self._load_unlocked()
            result: dict[str, dict[str, dict[str, Any]]] = {}
            for solution, solution_entry in document["solutions"].items():
                if not isinstance(solution, str) or not isinstance(
                    solution_entry, dict
                ):
                    continue
                evaluations = solution_entry.get("evaluations")
                if not isinstance(evaluations, dict):
                    continue
                result[solution] = {
                    seed: deepcopy(evaluation)
                    for seed, evaluation in evaluations.items()
                    if isinstance(seed, str) and isinstance(evaluation, dict)
                }
            return result

    def seed_order(self) -> list[str]:
        """Return seeds in the order they were first added to the scoreboard."""
        with _LOCK:
            document = self._load_unlocked()
            return list(document["seed_order"])

    def save(self, solution: str, seed: str, value: dict[str, Any]) -> None:
        with _LOCK:
            document = self._load_unlocked()
            solutions = document["solutions"]
            solution_entry = solutions.setdefault(
                solution, {"latest_seed": seed, "evaluations": {}}
            )
            if not isinstance(solution_entry, dict):
                solution_entry = {"latest_seed": seed, "evaluations": {}}
                solutions[solution] = solution_entry
            evaluations = solution_entry.setdefault("evaluations", {})
            if not isinstance(evaluations, dict):
                evaluations = {}
                solution_entry["evaluations"] = evaluations

            seed_order = document["seed_order"]
            if seed not in seed_order:
                seed_order.append(seed)

            now = datetime.now(UTC).isoformat()
            previous = evaluations.get(seed)
            stored = deepcopy(value)
            if isinstance(previous, dict) and isinstance(
                previous.get("created_at"), str
            ):
                stored["created_at"] = previous["created_at"]
            elif isinstance(previous, dict) and isinstance(
                previous.get("updated_at"), str
            ):
                stored["created_at"] = previous["updated_at"]
            else:
                stored["created_at"] = now
            stored["updated_at"] = now
            evaluations[seed] = stored
            solution_entry["latest_seed"] = seed
            document["latest_solution"] = solution
            self._write_unlocked(document)
