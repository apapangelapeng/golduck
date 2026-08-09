#!/usr/bin/env python3
"""Check whether a Max151 observation depends on bits outside its local table."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_max151_augmentations import (
    CONTEXT_FIRST_BIT,
    CONTEXT_LENGTH,
    DEFAULT_BGOLLY,
    WINDOWS,
    Candidate,
    _baseline_candidate,
    _evaluate_shard,
)

DEFAULT_REPORT = ROOT / "analysis/level2/max151-locality-audit.json"


def _parse_int_list(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in value.split(",") if item)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from error
    if not result or any(number < 0 for number in result):
        raise argparse.ArgumentTypeError("values must be nonnegative integers")
    return result


def _geometry(secret: int) -> tuple[int, ...]:
    parity_generator = random.Random(secret)
    result: list[int] = []
    previous = 0
    parity = 0
    for bit in range(64):
        occupied = (secret >> bit) & 1
        if occupied and not previous:
            parity = parity_generator.randrange(2)
        result.append(parity + 1 if occupied else 0)
        previous = occupied
    return tuple(result)


def _requests(
    candidates: tuple[Candidate, ...],
    full_geometries: tuple[tuple[int, ...], ...],
    bgolly: Path,
    workers: int,
) -> list[tuple[Candidate, tuple[tuple[int, tuple[int, ...]], ...], str, int]]:
    indexed: list[tuple[int, tuple[int, ...]]] = []
    for sample, full in enumerate(full_geometries):
        local = full[CONTEXT_FIRST_BIT : CONTEXT_FIRST_BIT + CONTEXT_LENGTH]
        indexed.extend(((2 * sample, local), (2 * sample + 1, full)))

    shard_count = min(len(indexed) // 2, workers * 4)
    requests = []
    for candidate in candidates:
        for shard in range(shard_count):
            sample_start = (len(full_geometries) * shard) // shard_count
            sample_end = (len(full_geometries) * (shard + 1)) // shard_count
            requests.append(
                (
                    candidate,
                    tuple(indexed[2 * sample_start : 2 * sample_end]),
                    str(bgolly),
                    1,
                )
            )
    return requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bgolly", type=Path, default=DEFAULT_BGOLLY)
    parser.add_argument("--generations", type=_parse_int_list, default=(1326, 1425, 1775))
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--workers", type=int, default=min(8, max(1, os.cpu_count() or 1)))
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x15110CA1)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if args.samples < 1 or args.workers < 1:
        parser.error("samples and workers must be positive")
    if not args.bgolly.exists():
        parser.error(f"bgolly not found: {args.bgolly}")

    generator = random.Random(args.seed)
    secrets = tuple(generator.getrandbits(64) for _ in range(args.samples))
    full_geometries = tuple(_geometry(secret) for secret in secrets)
    candidates = tuple(_baseline_candidate(generation) for generation in args.generations)
    requests = _requests(candidates, full_geometries, args.bgolly, args.workers)
    print(
        f"auditing {len(candidates)} generations on {args.samples} full secrets "
        f"with {args.workers} workers",
        flush=True,
    )

    started = time.monotonic()
    records: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    worker_seconds: dict[str, float] = defaultdict(float)
    with concurrent.futures.ThreadPoolExecutor(args.workers) as executor:
        futures = [executor.submit(_evaluate_shard, request) for request in requests]
        for position, future in enumerate(concurrent.futures.as_completed(futures), 1):
            candidate_id, shard_records, elapsed = future.result()
            worker_seconds[candidate_id] += elapsed
            records[candidate_id].update(shard_records)
            if position % max(1, len(futures) // 12) == 0 or position == len(futures):
                print(f"{position}/{len(futures)} locality shards complete", flush=True)

    rows: list[dict[str, object]] = []
    for candidate in candidates:
        candidate_records = records[candidate.candidate_id]
        if len(candidate_records) != 2 * args.samples:
            raise RuntimeError(
                f"{candidate.candidate_id} returned {len(candidate_records)} records"
            )
        windows: dict[str, dict[str, object]] = {}
        for window in WINDOWS:
            mismatches = [
                sample
                for sample in range(args.samples)
                if candidate_records[2 * sample][window]
                != candidate_records[2 * sample + 1][window]
            ]
            windows[window] = {
                "mismatches": len(mismatches),
                "mismatch_fraction": len(mismatches) / args.samples,
                "first_mismatch_secrets": [
                    f"{secrets[sample]:016x}" for sample in mismatches[:8]
                ],
            }
        row = {
            "generation": candidate.generation,
            "samples": args.samples,
            "windows": windows,
            "worker_seconds": worker_seconds[candidate.candidate_id],
        }
        rows.append(row)
        print(
            f"g{candidate.generation}: "
            + ", ".join(
                f"{window}={cast(dict[str, object], values)['mismatches']}/{args.samples}"
                for window, values in windows.items()
            ),
            flush=True,
        )

    payload = {
        "format": "golduck-max151-locality-audit",
        "version": 1,
        "seed": args.seed,
        "local_context_bits": [
            CONTEXT_FIRST_BIT,
            CONTEXT_FIRST_BIT + CONTEXT_LENGTH - 1,
        ],
        "rows": rows,
        "elapsed_seconds": time.monotonic() - started,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
