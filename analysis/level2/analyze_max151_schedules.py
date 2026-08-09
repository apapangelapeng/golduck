#!/usr/bin/env python3
"""Search six-run schedules for an exhaustively calibrated Max151 channel."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_max151_schedule import Max151Model

DEFAULT_CHECKPOINT = ROOT / "analysis/level2/max151-augmentation-search.jsonl"
DEFAULT_REPORT = ROOT / "analysis/level2/max151-six-run-schedules.json"
SECRET_BITS = 64
CONTEXT_BITS = 12
ALL_BITS = (1 << SECRET_BITS) - 1
LOCAL_BITS = (1 << CONTEXT_BITS) - 1


@dataclass(frozen=True)
class Observation:
    label: Hashable
    known: int
    value: int


@dataclass(frozen=True)
class ScheduleScore:
    starts: tuple[int, ...]
    mean_known: float
    min_known: int
    median_known: float
    full_fraction: float
    mean_unknown: float


class ExhaustiveChannel:
    def __init__(
        self,
        contexts: tuple[tuple[int, ...], ...],
        labels: tuple[Hashable, ...],
    ) -> None:
        if len(contexts) != len(labels):
            raise ValueError("contexts and labels are not aligned")
        self.contexts = contexts
        self.labels = labels
        self.rank = {context: index for index, context in enumerate(contexts)}

        masks_by_label: dict[Hashable, list[int]] = defaultdict(list)
        for context, label in zip(contexts, labels):
            masks_by_label[label].append(
                sum(bool(symbol) << bit for bit, symbol in enumerate(context))
            )
        self.forced: dict[Hashable, tuple[int, int]] = {}
        for label, masks in masks_by_label.items():
            known = LOCAL_BITS
            first = masks[0]
            for mask in masks[1:]:
                known &= ~(first ^ mask)
            known &= LOCAL_BITS
            self.forced[label] = known, first & known

    @staticmethod
    def geometry(secret: int) -> tuple[int, ...]:
        parity_generator = random.Random(secret)
        result: list[int] = []
        previous = 0
        parity = 0
        for bit in range(SECRET_BITS):
            occupied = (secret >> bit) & 1
            if occupied and not previous:
                parity = parity_generator.randrange(2)
            result.append(parity + 1 if occupied else 0)
            previous = occupied
        return tuple(result)

    def label(self, geometry: tuple[int, ...], start: int) -> Hashable:
        context = tuple(
            geometry[bit] if 0 <= bit < SECRET_BITS else 0
            for bit in range(start - 3, start + 9)
        )
        return self.labels[self.rank[context]]

    def observation(self, geometry: tuple[int, ...], start: int) -> Observation:
        label = self.label(geometry, start)
        local_known, local_value = self.forced[label]
        global_known = 0
        global_value = 0
        for position in range(CONTEXT_BITS):
            bit = start - 3 + position
            if not 0 <= bit < SECRET_BITS or not (local_known >> position) & 1:
                continue
            global_known |= 1 << bit
            if (local_value >> position) & 1:
                global_value |= 1 << bit
        return Observation(label, global_known, global_value)

    def transcript(
        self, secret: int, starts: tuple[int, ...]
    ) -> tuple[Hashable, ...]:
        geometry = self.geometry(secret)
        return tuple(self.label(geometry, start) for start in starts)


def load_exhaustive_row(
    checkpoint: Path, candidate_id: str, window: str
) -> tuple[tuple[str, ...], dict[str, object]]:
    selected: dict[str, object] | None = None
    with checkpoint.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("stage") == "exhaustive"
                and row.get("candidate", {}).get("candidate_id") == candidate_id
            ):
                selected = row
    if selected is None:
        raise ValueError(f"no exhaustive row for {candidate_id!r} in {checkpoint}")
    signatures = selected.get("signatures")
    if not isinstance(signatures, dict) or window not in signatures:
        raise ValueError(f"exhaustive row has no {window!r} signatures")
    labels = tuple(map(str, cast(list[object], signatures[window])))
    return labels, selected


def candidate_schedules(
    run_count: int, start_min: int, start_max: int
) -> tuple[tuple[int, ...], ...]:
    """Enumerate schedules whose 12-bit windows cover bits zero through 63."""

    schedules = []
    for starts in itertools.combinations(range(start_min, start_max + 1), run_count):
        if starts[0] > 3 or starts[-1] < 55:
            continue
        if any(
            right - left > CONTEXT_BITS
            for left, right in itertools.pairwise(starts)
        ):
            continue
        schedules.append(starts)
    return tuple(schedules)


def score_schedules(
    channel: ExhaustiveChannel,
    schedules: Iterable[tuple[int, ...]],
    secrets: tuple[int, ...],
    starts: tuple[int, ...],
) -> list[ScheduleScore]:
    observations: dict[tuple[int, int], Observation] = {}
    for sample, secret in enumerate(secrets):
        geometry = channel.geometry(secret)
        for start in starts:
            observations[(sample, start)] = channel.observation(geometry, start)

    result: list[ScheduleScore] = []
    for schedule in schedules:
        known_counts: list[int] = []
        for sample in range(len(secrets)):
            known = 0
            for start in schedule:
                known |= observations[(sample, start)].known
            known_counts.append(known.bit_count())
        result.append(
            ScheduleScore(
                starts=schedule,
                mean_known=statistics.fmean(known_counts),
                min_known=min(known_counts),
                median_known=statistics.median(known_counts),
                full_fraction=sum(count == SECRET_BITS for count in known_counts)
                / len(known_counts),
                mean_unknown=SECRET_BITS - statistics.fmean(known_counts),
            )
        )
    result.sort(
        key=lambda score: (
            score.full_fraction,
            score.mean_known,
            score.min_known,
            score.median_known,
        ),
        reverse=True,
    )
    return result


def merge_observations(observations: Iterable[Observation]) -> tuple[int, int]:
    known = 0
    value = 0
    for observation in observations:
        conflict = known & observation.known & (value ^ observation.value)
        if conflict:
            raise AssertionError("exhaustive observations contain conflicting literals")
        value = (value & ~observation.known) | observation.value
        known |= observation.known
    return known, value & known


def exact_survivors(
    channel: ExhaustiveChannel,
    secret: int,
    starts: tuple[int, ...],
    max_unknown: int,
) -> tuple[int, int] | None:
    geometry = channel.geometry(secret)
    observations = tuple(channel.observation(geometry, start) for start in starts)
    known, value = merge_observations(observations)
    unknown = ALL_BITS ^ known
    unknown_count = unknown.bit_count()
    if unknown_count > max_unknown:
        return None
    expected = tuple(observation.label for observation in observations)
    survivors = 0
    subset = unknown
    while True:
        candidate = value | subset
        if channel.transcript(candidate, starts) == expected:
            survivors += 1
        if not subset:
            break
        subset = (subset - 1) & unknown
    if channel.transcript(secret, starts) != expected or not (secret & known) == value:
        raise AssertionError("the true secret was eliminated")
    return unknown_count, survivors


def sieve_schedules(
    channel: ExhaustiveChannel,
    schedules: list[ScheduleScore],
    secrets: tuple[int, ...],
    max_unknown: int,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for position, schedule in enumerate(schedules, 1):
        rows = [
            exact_survivors(channel, secret, schedule.starts, max_unknown)
            for secret in secrets
        ]
        evaluated = [row for row in rows if row is not None]
        survivor_counts = [row[1] for row in evaluated]
        unknown_counts = [row[0] for row in evaluated]
        result.append(
            {
                "starts": schedule.starts,
                "direct_mean_known": schedule.mean_known,
                "direct_min_known": schedule.min_known,
                "samples": len(rows),
                "sieved_samples": len(evaluated),
                "skipped_samples": len(rows) - len(evaluated),
                "unique_samples": sum(count == 1 for count in survivor_counts),
                "unique_fraction": (
                    sum(count == 1 for count in survivor_counts) / len(evaluated)
                    if evaluated
                    else 0.0
                ),
                "median_survivors": (
                    statistics.median(survivor_counts) if survivor_counts else None
                ),
                "max_survivors": max(survivor_counts, default=None),
                "median_unknown": (
                    statistics.median(unknown_counts) if unknown_counts else None
                ),
                "max_unknown_evaluated": max(unknown_counts, default=None),
            }
        )
        print(
            f"sieve {position}/{len(schedules)} starts={schedule.starts} "
            f"evaluated={len(evaluated)}/{len(rows)} "
            f"unique={sum(count == 1 for count in survivor_counts)} "
            f"median/max survivors="
            f"{statistics.median(survivor_counts) if survivor_counts else '-'}"
            f"/{max(survivor_counts, default='-')}",
            flush=True,
        )
    result.sort(
        key=lambda row: (
            cast(float, row["unique_fraction"]),
            -cast(int, row["skipped_samples"]),
            -cast(float, row["median_survivors"] or math.inf),
            -cast(int, row["max_survivors"] or sys.maxsize),
        ),
        reverse=True,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--candidate", default="baseline-g1775")
    parser.add_argument("--window", choices=("legacy", "wide", "full"), default="legacy")
    parser.add_argument("--source-current", action="store_true")
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--start-min", type=int, default=-3)
    parser.add_argument("--start-max", type=int, default=61)
    parser.add_argument("--samples", type=int, default=512)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x151_6)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--sieve-samples", type=int, default=64)
    parser.add_argument("--max-unknown", type=int, default=20)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if args.runs < 1 or args.samples < 1 or args.top < 1 or args.sieve_samples < 1:
        parser.error("runs, samples, top, and sieve-samples must be positive")
    if args.start_min > args.start_max:
        parser.error("start-min must not exceed start-max")

    started = time.monotonic()
    production_model = Max151Model()
    source_row: dict[str, object] | None = None
    if args.source_current:
        labels: tuple[Hashable, ...] = tuple(production_model.classes)
        source_name = "source-current-g1326"
    else:
        loaded_labels, source_row = load_exhaustive_row(
            args.checkpoint, args.candidate, args.window
        )
        labels = loaded_labels
        source_name = args.candidate
    channel = ExhaustiveChannel(production_model.contexts, labels)
    print(
        f"loaded {source_name}/{args.window}: contexts={len(channel.contexts)} "
        f"classes={len(set(channel.labels))}",
        flush=True,
    )

    schedules = candidate_schedules(args.runs, args.start_min, args.start_max)
    if not schedules:
        parser.error("no covering schedules exist in the requested start range")
    print(f"enumerated {len(schedules)} covering schedules", flush=True)

    generator = random.Random(args.seed)
    secrets = tuple(generator.getrandbits(SECRET_BITS) for _ in range(args.samples))
    allowed_starts = tuple(range(args.start_min, args.start_max + 1))
    scored = score_schedules(channel, schedules, secrets, allowed_starts)
    print("top direct-literal schedules:", flush=True)
    for score in scored[: min(12, len(scored))]:
        print(
            f"  {score.starts} mean={score.mean_known:.3f} "
            f"median={score.median_known:g} min={score.min_known} "
            f"full={score.full_fraction:.3%}",
            flush=True,
        )

    sieve_secrets = secrets[: min(args.sieve_samples, len(secrets))]
    sieved = sieve_schedules(
        channel,
        scored[: min(args.top, len(scored))],
        sieve_secrets,
        args.max_unknown,
    )
    payload: dict[str, object] = {
        "format": "golduck-max151-schedule-search",
        "version": 1,
        "source": source_name,
        "window": args.window,
        "classes": len(set(channel.labels)),
        "contexts": len(channel.contexts),
        "run_count": args.runs,
        "start_range": [args.start_min, args.start_max],
        "schedule_count": len(schedules),
        "sample_count": len(secrets),
        "seed": args.seed,
        "top_direct": [score.__dict__ for score in scored[:100]],
        "sieve": sieved,
        "elapsed_seconds": time.monotonic() - started,
    }
    if source_row is not None:
        payload["source_metrics"] = source_row.get("metrics")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
