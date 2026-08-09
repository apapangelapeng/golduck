"""Seed-aware, streaming solution evaluation for the local visualizer.

Life evolution prefers a native bgolly when one is available and falls back
to a compact row-bitset B3/S23 engine.  Scoring still goes through the
production ``Sim`` implementation and the normal Wasm host ABI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import multiprocessing
import os
import threading
import time
from collections.abc import Callable, Collection
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sortedcontainers import SortedDict, SortedList

import runner_core
from golduck.errors import ExecutionError
from golduck.levels import LEVELS
from golduck.rle import RLEPattern, encode_rle, parse_rle
from golduck.sim import (
    Sim,
    _corner_blocks,
    _corner_regions,
    _score_level,
    _score_submission,
)

EventCallback = Callable[[dict[str, object]], None]
UINT64_MASK = (1 << 64) - 1
FULL_KNOWN_MASK = UINT64_MASK
VISUALIZER_TIMEOUT_SECONDS = 300.0
MAX_PLANNING_ROUNDS = 128
RunKey = tuple[int, str, int]
RunCache = dict[RunKey, str]


def _selected_level_set(levels: Collection[int] | None) -> frozenset[int]:
    """Return and validate the levels whose Life runs should be evaluated."""
    selected = frozenset(LEVELS) if levels is None else frozenset(levels)
    if not selected:
        raise ValueError("at least one level must be selected")
    unknown = selected.difference(LEVELS)
    if unknown:
        raise ValueError(f"unknown level {min(unknown)}")
    return selected


def _empty_canvas_output(level: int) -> str:
    """Return an integrity-valid inert response for an unselected level run."""
    _, _, width, height = LEVELS[level](0).get_canvas_rect()
    return encode_rle(_corner_blocks(width, height))


class EvaluationCancelled(RuntimeError):
    """Raised when the browser abandons an in-flight evaluation."""


def parse_seed(value: str) -> bytes:
    """Parse the visualizer's canonical 128-bit challenge seed."""
    normalized = value.strip().lower()
    normalized = normalized.removeprefix("0x")
    if len(normalized) != 32:
        raise ValueError("seed must contain exactly 32 hexadecimal digits")
    try:
        seed = bytes.fromhex(normalized)
    except ValueError:
        raise ValueError("seed must be a 128-bit hexadecimal value") from None
    return seed


def secret_for_seed(seed: bytes, level: int) -> int:
    """Derive a level secret exactly as the production simulator does."""
    digest = hmac.new(seed, f"secret_{level}".encode("ascii"), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "little")


def _hex64(value: int) -> str:
    return f"0x{value & UINT64_MASK:016x}"


def _pattern_cells(
    pattern: RLEPattern, origin_x: int, origin_y: int
) -> list[list[int]]:
    cells: list[list[int]] = []
    for row_y, intervals in pattern.rows.items():
        for start, end in intervals:
            cells.extend([origin_x + x, origin_y + row_y] for x in range(start, end))
    return cells


def _pattern_to_bit_rows(pattern: RLEPattern) -> dict[int, int]:
    rows: dict[int, int] = {}
    for y, intervals in pattern.rows.items():
        bits = 0
        for start, end in intervals:
            bits |= ((1 << (end - start)) - 1) << start
        if bits:
            rows[y] = bits
    return rows


def _trailing_ones(value: int) -> int:
    return (value ^ (value + 1)).bit_length() - 1


def _bit_rows_to_pattern(rows: dict[int, int], width: int, height: int) -> RLEPattern:
    interval_rows: dict[int, SortedList[tuple[int, int]]] = {}
    for y, original_bits in rows.items():
        bits = original_bits
        intervals: list[tuple[int, int]] = []
        while bits:
            start = (bits & -bits).bit_length() - 1
            run_length = _trailing_ones(bits >> start)
            end = start + run_length
            intervals.append((start, end))
            bits &= ~(((1 << run_length) - 1) << start)
        if intervals:
            interval_rows[y] = SortedList(intervals)
    return RLEPattern(
        width=width,
        height=height,
        rows=SortedDict(interval_rows),
    )


def evolve_life(
    pattern: RLEPattern,
    generations: int,
    cancelled: threading.Event | None = None,
    deadline: float | None = None,
) -> RLEPattern:
    """Evolve a finite B3/S23 pattern using bit-packed rows.

    Canvas corner markers are stable blocks placed thousands of cells away
    from the challenge.  Removing them during evolution keeps the active row
    set compact; they are restored before the normal simulator integrity
    checks run. Active rows are translated into a narrow horizontal window
    and periodically rebased, so Python integers scale with the live pattern
    rather than the full 10,000-cell canvas.
    """
    rows = _pattern_to_bit_rows(pattern)
    corners = (
        _corner_regions(pattern.width, pattern.height)
        if pattern.width >= 2 and pattern.height >= 2
        else ()
    )
    corner_cells: list[tuple[int, int]] = []
    for corner in corners:
        for x, y in corner:
            cell_mask = 1 << x
            original_row = rows.get(y, 0)
            if not original_row & cell_mask:
                continue
            corner_cells.append((x, y))
            row = original_row & ~cell_mask
            if row:
                rows[y] = row
            else:
                rows.pop(y, None)

    # Translation is free in Life. Keep 128 blank columns to the left of the
    # active pattern and rebase every 32 generations before a signal could
    # reach the artificial local edge. This sharply reduces both bigint work
    # and temporary allocation for sparse patterns near the canvas center.
    x_origin = 0
    if rows:
        occupied_columns = 0
        for bits in rows.values():
            occupied_columns |= bits
        minimum_x = (occupied_columns & -occupied_columns).bit_length() - 1
        x_origin = max(0, minimum_x - 128)
        if x_origin:
            rows = {y: bits >> x_origin for y, bits in rows.items()}

    width_mask = (1 << (pattern.width - x_origin)) - 1
    for generation in range(generations):
        if generation % 32 == 0:
            if cancelled is not None and cancelled.is_set():
                raise EvaluationCancelled("evaluation cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("evaluation timed out")
        if not rows:
            break

        if generation % 32 == 0:
            occupied_columns = 0
            for bits in rows.values():
                occupied_columns |= bits
            minimum_x = (occupied_columns & -occupied_columns).bit_length() - 1
            if minimum_x < 64 and x_origin:
                shift = min(256, x_origin)
                x_origin -= shift
                rows = {y: bits << shift for y, bits in rows.items()}
                width_mask = (1 << (pattern.width - x_origin)) - 1
            elif minimum_x > 512:
                shift = minimum_x - 128
                x_origin += shift
                rows = {y: bits >> shift for y, bits in rows.items()}
                width_mask = (1 << (pattern.width - x_origin)) - 1

        candidate_rows = set(rows)
        candidate_rows.update(y - 1 for y in rows)
        candidate_rows.update(y + 1 for y in rows)
        next_rows: dict[int, int] = {}
        get_row = rows.get
        for y in candidate_rows:
            above = get_row(y - 1, 0)
            current = get_row(y, 0)
            below = get_row(y + 1, 0)

            # A carry-save tree counts all eight neighbors in about half the
            # bigint operations of incrementing four bit planes eight times.
            # `low` and `second` are the 1- and 2-bit count planes; `high`
            # marks counts of four or more. B3/S23 then reduces to one mask.
            above_left = above << 1
            above_right = above >> 1
            above_xor = above_left ^ above
            above_low = above_xor ^ above_right
            above_high = (above_left & above) | (above_right & above_xor)

            below_left = below << 1
            below_right = below >> 1
            below_xor = below_left ^ below
            below_low = below_xor ^ below_right
            below_high = (below_left & below) | (below_right & below_xor)

            middle_left = current << 1
            middle_right = current >> 1
            low_xor = above_low ^ below_low
            low = low_xor ^ middle_left
            low_carry = (above_low & below_low) | (middle_left & low_xor)
            middle_carry = low & middle_right
            low ^= middle_right

            second_xor = above_high ^ below_high
            second = second_xor ^ low_carry
            high = (above_high & below_high) | (low_carry & second_xor)
            high |= second & middle_carry
            second ^= middle_carry

            next_row = (second & ~high & (low | current)) & width_mask
            if next_row:
                next_rows[y] = next_row
        rows = next_rows

    if x_origin:
        rows = {y: bits << x_origin for y, bits in rows.items()}
    for x, y in corner_cells:
        rows[y] = rows.get(y, 0) | (1 << x)
    return _bit_rows_to_pattern(rows, pattern.width, pattern.height)


def _run_key(level: int, pattern: bytes | str, generations: int) -> RunKey:
    pattern_text = pattern.decode("ascii") if isinstance(pattern, bytes) else pattern
    return (level, pattern_text, generations)


class PlanningSim(Sim):
    """Replay a Wasm and discover run requests without blocking on Life.

    Known requests receive their real cached canvas. Unknown requests receive
    an empty, integrity-valid canvas and are collected for the next parallel
    simulation batch. Replaying from the beginning after each batch preserves
    correctness when later calls depend on earlier viewing output.
    """

    def __init__(self, seed: bytes, output_cache: RunCache) -> None:
        super().__init__(seed_hex=seed, enabled_stages=-1)
        self.output_cache = output_cache
        self.pending: dict[RunKey, None] = {}
        self._active_key: RunKey | None = None
        self._empty_canvases: dict[int, str] = {}

    def run(self, level: int, pattern: bytes, generations: int) -> bytes:
        key = _run_key(level, pattern, generations)
        self._active_key = key
        if key not in self.output_cache:
            self.pending.setdefault(key, None)
        return super().run(level, pattern, generations)

    def _run_bgolly(self, level_obj: Any, pattern: RLEPattern, generations: int) -> str:
        if self._active_key is None:
            raise RuntimeError("planning run has no active request")
        cached = self.output_cache.get(self._active_key)
        if cached is not None:
            return cached

        level = self._active_key[0]
        if level not in self._empty_canvases:
            _, _, width, height = level_obj.get_canvas_rect()
            self._empty_canvases[level] = encode_rle(_corner_blocks(width, height))
        return self._empty_canvases[level]


class PortableRunSim(Sim):
    """Simulate one isolated request inside a process-pool worker."""

    def __init__(self, seed: bytes) -> None:
        super().__init__(seed_hex=seed, enabled_stages=-1)
        self.deadline = time.monotonic() + VISUALIZER_TIMEOUT_SECONDS
        self.canvas_rle: str | None = None

    def _run_bgolly(
        self,
        level_obj: Any,
        pattern: RLEPattern,
        generations: int,
    ) -> RLEPattern:
        evolved = evolve_life(pattern, generations, deadline=self.deadline)
        self.canvas_rle = encode_rle(evolved)
        return evolved


class ExternalRunSim(Sim):
    """Simulate one request with the platform's native bgolly binary."""

    def __init__(self, seed: bytes) -> None:
        super().__init__(seed_hex=seed, enabled_stages=-1)
        self.canvas_rle: str | None = None

    def _run_bgolly(
        self,
        level_obj: Any,
        pattern: RLEPattern,
        generations: int,
    ) -> str:
        output = super()._run_bgolly(level_obj, pattern, generations)
        self.canvas_rle = output
        return output


def _simulate_request_portable(seed: bytes, key: RunKey) -> tuple[RunKey, str]:
    level, pattern_text, generations = key
    sim = PortableRunSim(seed)
    sim.run(level, pattern_text.encode("ascii"), generations)
    if sim.canvas_rle is None:
        raise RuntimeError("parallel simulation produced no canvas")
    return key, sim.canvas_rle


def _simulate_request_external(seed: bytes, key: RunKey) -> tuple[RunKey, str]:
    level, pattern_text, generations = key
    sim = ExternalRunSim(seed)
    sim.run(level, pattern_text.encode("ascii"), generations)
    if sim.canvas_rle is None:
        raise RuntimeError("native simulation produced no canvas")
    return key, sim.canvas_rle


_external_bgolly_usable: bool | None = None


def _simulate_request(seed: bytes, key: RunKey) -> tuple[RunKey, str]:
    """Use native HashLife when possible, with a per-worker portable fallback."""
    global _external_bgolly_usable
    if _external_bgolly_usable is not False:
        try:
            result = _simulate_request_external(seed, key)
        except (ExecutionError, OSError):
            # Process-pool workers are reused, so remember an unavailable or
            # incompatible binary instead of paying its startup cost per run.
            _external_bgolly_usable = False
        else:
            _external_bgolly_usable = True
            return result
    return _simulate_request_portable(seed, key)


def _check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise EvaluationCancelled("evaluation cancelled")


def _planning_pass(
    wasm_bytes: bytes,
    seed: bytes,
    output_cache: RunCache,
) -> tuple[list[RunKey], Exception | None]:
    sim = PlanningSim(seed, output_cache)
    error: Exception | None = None
    try:
        runner_core.adopt_run_deadline(time.monotonic() + VISUALIZER_TIMEOUT_SECONDS)
        runner_core.WasmHost(sim, wasm_bytes).run()
    except Exception as exc:  # replay errors may disappear after cache fill
        error = exc
    return list(sim.pending), error


def prepare_parallel_outputs(
    wasm_bytes: bytes,
    seed: bytes,
    on_event: EventCallback,
    cancelled: threading.Event | None = None,
    parallel_workers: int | None = None,
    selected_levels: Collection[int] | None = None,
) -> RunCache:
    """Resolve selected run requests and stub the others in planning order."""
    if parallel_workers is not None and parallel_workers < 1:
        raise ValueError("parallel_workers must be at least 1")
    evaluated_levels = _selected_level_set(selected_levels)
    output_cache: RunCache = {}
    empty_outputs: dict[int, str] = {}
    executor: ProcessPoolExecutor | None = None
    try:
        for round_number in range(1, MAX_PLANNING_ROUNDS + 1):
            _check_cancelled(cancelled)
            pending, planning_error = _planning_pass(wasm_bytes, seed, output_cache)
            pending = [key for key in pending if key not in output_cache]
            if not pending:
                if planning_error is not None:
                    raise planning_error
                return output_cache

            simulation_pending: list[RunKey] = []
            for key in pending:
                level = key[0]
                if level in evaluated_levels:
                    simulation_pending.append(key)
                    continue
                if level not in empty_outputs:
                    empty_outputs[level] = _empty_canvas_output(level)
                output_cache[key] = empty_outputs[level]

            # Replaying after inert responses may reveal later selected calls.
            if not simulation_pending:
                continue

            if executor is None:
                worker_count = parallel_workers or max(1, min(32, os.cpu_count() or 1))
                executor = ProcessPoolExecutor(
                    max_workers=worker_count,
                    mp_context=multiprocessing.get_context("spawn"),
                )

            level_counts: dict[int, int] = {}
            for level, _, _ in simulation_pending:
                level_counts[level] = level_counts.get(level, 0) + 1
            on_event(
                {
                    "type": "parallel_batch_started",
                    "round": round_number,
                    "total": len(simulation_pending),
                    "levels": level_counts,
                }
            )

            futures = {
                executor.submit(_simulate_request, seed, key): key
                for key in simulation_pending
            }
            for completed, future in enumerate(as_completed(futures), 1):
                _check_cancelled(cancelled)
                key, canvas_rle = future.result()
                output_cache[key] = canvas_rle
                on_event(
                    {
                        "type": "parallel_progress",
                        "round": round_number,
                        "completed": completed,
                        "total": len(simulation_pending),
                        "level": key[0],
                    }
                )
    finally:
        if executor is not None:
            executor.shutdown(
                wait=cancelled is None or not cancelled.is_set(),
                cancel_futures=True,
            )
    raise RuntimeError("adaptive run planning did not converge")


class LiveEvaluationSim(Sim):
    """Production scorer with observable runs and a portable Life engine."""

    def __init__(
        self,
        seed: bytes,
        on_event: EventCallback,
        cancelled: threading.Event | None = None,
        output_cache: RunCache | None = None,
        selected_levels: Collection[int] | None = None,
    ) -> None:
        super().__init__(seed_hex=seed, enabled_stages=-1)
        self.on_event = on_event
        self.cancelled = cancelled
        self.deadline = time.monotonic() + VISUALIZER_TIMEOUT_SECONDS
        self.output_cache = output_cache
        self.selected_levels = _selected_level_set(selected_levels)
        self._active_key: RunKey | None = None
        self.runs: list[dict[str, object]] = []
        self.submission_events: list[dict[str, object]] = []

    def _check_cancelled(self) -> None:
        if self.cancelled is not None and self.cancelled.is_set():
            raise EvaluationCancelled("evaluation cancelled")

    def _emit(self, event: dict[str, object]) -> None:
        self._check_cancelled()
        level = event.get("level")
        if isinstance(level, int) and level not in self.selected_levels:
            return
        event["totals"] = self.totals_snapshot()
        self.on_event(event)

    def _run_bgolly(
        self,
        level_obj: Any,
        pattern: RLEPattern,
        generations: int,
    ) -> str | RLEPattern:
        self._check_cancelled()
        if self.output_cache is not None:
            if self._active_key is None or self._active_key not in self.output_cache:
                raise RuntimeError("final replay requested an unplanned run")
            return self.output_cache[self._active_key]
        return evolve_life(pattern, generations, self.cancelled, deadline=self.deadline)

    def run(self, level: int, pattern: bytes, generations: int) -> bytes:
        self._check_cancelled()
        pattern_text = pattern.decode("ascii")
        parsed = parse_rle(pattern_text)
        self._active_key = _run_key(level, pattern_text, generations)
        level_obj = self._get_level(level)
        level_run = self._run_counts.get(level, 0) + 1
        self._emit(
            {
                "type": "run_started",
                "level": level,
                "run": level_run,
                "max_runs": level_obj.get_max_runs(),
                "generations": generations,
                "cell_count": sum(
                    end - start
                    for intervals in parsed.rows.values()
                    for start, end in intervals
                ),
            }
        )

        output = super().run(level, pattern, generations)
        origin_x, origin_y, _, _ = level_obj.get_contestant_rect()
        entry: dict[str, object] = {
            "level": level,
            "level_run": level_run,
            "generations": generations,
            "rle": pattern_text,
            "cells": _pattern_cells(parsed, origin_x, origin_y),
            "size": {"w": parsed.width, "h": parsed.height},
            "cell_count": sum(
                end - start
                for intervals in parsed.rows.values()
                for start, end in intervals
            ),
        }
        if level in self.selected_levels:
            self.runs.append(entry)
        self._emit(
            {
                "type": "run_complete",
                "level": level,
                "run": entry,
                "level_score": self.level_snapshot(level),
            }
        )
        return output

    def submit(self, level: int, value: int, known_mask: int, guess_mask: int) -> bool:
        result = super().submit(level, value, known_mask, guess_mask)
        submission = self._submissions[level]
        entry: dict[str, object] = {
            "level": level,
            "value": _hex64(submission.submission),
            "known_mask": _hex64(submission.known_mask),
            "guess_mask": _hex64(submission.guess_mask),
            "accepted": bool(result),
        }
        if level in self.selected_levels:
            self.submission_events.append(entry)
        self._emit(
            {
                "type": "submission",
                "level": level,
                "submission": entry,
                "level_score": self.level_snapshot(level),
            }
        )
        return result

    def level_snapshot(self, level: int) -> dict[str, object]:
        level_obj = self._get_level(level)
        stats = self._run_stats.get(level, [])
        performance_score, detail = _score_level(level_obj, stats)
        bonuses = (
            float(detail["run_bonus"])
            + float(detail["density_bonus"])
            + float(detail["generations_bonus"])
        )
        snapshot: dict[str, object] = {
            "level": level,
            "secret": _hex64(level_obj.secret),
            "runs_completed": len(stats),
            "max_runs": level_obj.get_max_runs(),
            "base_score": performance_score - bonuses,
            "run_bonus": detail["run_bonus"],
            "density_bonus": detail["density_bonus"],
            "generations_bonus": detail["generations_bonus"],
            "performance_score": performance_score,
            "potential_score": performance_score + 100_000.0,
            "submitted": False,
            "known_bits": 0,
            "guess_bits": 0,
            "known_weight": None,
            "guess_weight": None,
            "answer_weight": None,
            "weighted_score": None,
            "exact_answer": False,
            "exact_bonus": 0.0,
            "score": None,
        }

        submission = self._submissions.get(level)
        if submission is None:
            return snapshot

        score, submission_detail = _score_submission(level_obj, stats, submission)
        wrong_bits = submission.submission ^ submission.secret
        known_correct = (
            bool(submission.known_mask) and (wrong_bits & submission.known_mask) == 0
        )
        exact_answer = known_correct and submission.known_mask == FULL_KNOWN_MASK
        known_weight = float(submission_detail["known_weight"])
        guess_weight = float(submission_detail["guess_weight"])
        exact_bonus = 100_000.0 if exact_answer else 0.0
        snapshot.update(
            {
                "submitted": True,
                "submission": _hex64(submission.submission),
                "known_mask": _hex64(submission.known_mask),
                "guess_mask": _hex64(submission.guess_mask),
                "known_bits": submission.known_mask.bit_count(),
                "guess_bits": submission.guess_mask.bit_count(),
                "known_correct": known_correct,
                "guess_correct_bits": (
                    ~wrong_bits & submission.guess_mask & UINT64_MASK
                ).bit_count(),
                "guess_wrong_bits": (wrong_bits & submission.guess_mask).bit_count(),
                "known_weight": known_weight,
                "guess_weight": guess_weight,
                "answer_weight": known_weight + guess_weight,
                "weighted_score": performance_score * (known_weight + guess_weight),
                "exact_answer": exact_answer,
                "exact_bonus": exact_bonus,
                "score": score,
            }
        )
        return snapshot

    def totals_snapshot(self) -> dict[str, object]:
        levels = [self.level_snapshot(level) for level in sorted(self.selected_levels)]
        actual = sum(
            float(level["score"]) for level in levels if level["score"] is not None
        )
        return {
            "score": actual,
            "potential_score": sum(float(level["potential_score"]) for level in levels),
            "submitted_levels": sum(bool(level["submitted"]) for level in levels),
            "level_count": len(levels),
        }


def evaluate_solution(
    wasm_path: Path,
    seed: bytes,
    on_event: EventCallback,
    cancelled: threading.Event | None = None,
    *,
    parallel_workers: int | None = None,
    selected_levels: Collection[int] | None = None,
) -> dict[str, object]:
    """Evaluate only selected levels while replaying inert outputs for the rest."""
    evaluated_levels = _selected_level_set(selected_levels)
    sim = LiveEvaluationSim(
        seed,
        on_event,
        cancelled,
        selected_levels=evaluated_levels,
    )
    initial_levels = [sim.level_snapshot(level) for level in sorted(evaluated_levels)]
    on_event(
        {
            "type": "start",
            "seed": seed.hex(),
            "levels": initial_levels,
            "totals": sim.totals_snapshot(),
        }
    )

    wasm_bytes = wasm_path.read_bytes()
    sim.output_cache = prepare_parallel_outputs(
        wasm_bytes,
        seed,
        on_event,
        cancelled,
        parallel_workers=parallel_workers,
        selected_levels=evaluated_levels,
    )
    _check_cancelled(cancelled)
    runner_core.adopt_run_deadline(time.monotonic() + VISUALIZER_TIMEOUT_SECONDS)
    host = runner_core.WasmHost(sim, wasm_bytes)
    host.run()
    if not sim.success:
        raise RuntimeError("solution returned without calling finalize()")

    detail = json.loads(sim.detail or "{}")
    if isinstance(detail, dict):
        level_details = detail.get("levels")
        if isinstance(level_details, dict):
            detail["levels"] = {
                key: value
                for key, value in level_details.items()
                if key.isdigit() and int(key) in evaluated_levels
            }
        detail["attempted"] = sum(
            level in sim._submissions for level in evaluated_levels
        )
    levels = [sim.level_snapshot(level) for level in sorted(evaluated_levels)]
    score = sum(float(level["score"]) for level in levels if level["score"] is not None)
    result: dict[str, object] = {
        "success": True,
        "score": score,
        "seed": seed.hex(),
        "levels": levels,
        "totals": sim.totals_snapshot(),
        "runs": sim.runs,
        "submissions": sim.submission_events,
        "detail": detail,
        "call_counts": host.call_counts,
    }
    on_event({"type": "complete", **result})
    return result
