#!/usr/bin/env python3
"""Score one Golduck Wasm artifact against many generated random seeds."""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import runner_core


_WASM_BYTES = b""
ENABLED_LEVELS = (3, 4)
ENABLED_STAGES = sum(1 << level for level in ENABLED_LEVELS)


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def init_worker(wasm_path: str) -> None:
    global _WASM_BYTES
    _WASM_BYTES = Path(wasm_path).read_bytes()


def score_seed(index: int, seed: bytes) -> dict[str, Any]:
    result = runner_core.run_per_team(
        {
            "artifact_b64": _WASM_BYTES,
            "seed_hex": seed,
            "team_id": None,
            "enabled_stages": ENABLED_STAGES,
        }
    )

    row: dict[str, Any] = {
        "index": index,
        "seed": seed.hex(),
        "success": bool(result.get("success")),
        "score": result.get("score"),
        "error": result.get("error"),
        "levels": {},
    }

    detail = result.get("detail")
    if detail:
        parsed = json.loads(detail)
        level_details = parsed.get("levels", {})
        for level in ENABLED_LEVELS:
            level_detail = level_details.get(str(level))
            if level_detail:
                known_weight = float(level_detail["known_weight"])
                run_bonus = float(level_detail["run_bonus"])
                row["levels"][level] = {
                    "known": known_weight,
                    "runs": round(100000.0 / run_bonus - 1.0),
                }
    return row


def print_row(done: int, count: int, row: dict[str, Any]) -> None:
    if row["success"]:
        level_results = "  ".join(
            f"L{level}-known={row['levels'][level]['known']:.6f}  "
            f"L{level}-runs={row['levels'][level]['runs']}"
            if level in row["levels"]
            else f"L{level}=not submitted"
            for level in ENABLED_LEVELS
        )
        print(
            f"[{done:>{len(str(count))}}/{count}] "
            f"seed={row['seed']}  score={row['score']:.2f}  "
            f"{level_results}"
        )
    else:
        print(
            f"[{done:>{len(str(count))}}/{count}] "
            f"seed={row['seed']}  FAILED: {row['error']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a Golduck Wasm artifact over N generated seeds."
    )
    parser.add_argument("wasm", type=Path, help="Wasm artifact to test")
    parser.add_argument("-n", "--count", type=positive_int, default=10)
    parser.add_argument(
        "-j",
        "--jobs",
        type=positive_int,
        default=min(4, os.cpu_count() or 1),
        help="parallel worker count (default: up to 4)",
    )
    parser.add_argument(
        "--rng-seed",
        type=lambda text: int(text, 0),
        default=None,
        help="reproduce a test-seed set; omitted means a fresh random set",
    )
    args = parser.parse_args()

    wasm_path = args.wasm.resolve()
    if not wasm_path.is_file():
        parser.error(f"file not found: {args.wasm}")

    rng_seed = (
        args.rng_seed
        if args.rng_seed is not None
        else random.SystemRandom().getrandbits(128)
    )
    rng = random.Random(rng_seed)
    seeds = [rng.getrandbits(128).to_bytes(16, "big") for _ in range(args.count)]
    jobs = min(args.jobs, args.count)

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=jobs,
        initializer=init_worker,
        initargs=(str(wasm_path),),
    ) as executor:
        futures = {
            executor.submit(score_seed, index, seed): index
            for index, seed in enumerate(seeds)
        }
        for done, future in enumerate(as_completed(futures), 1):
            row = future.result()
            rows.append(row)
            print_row(done, args.count, row)

    elapsed = time.perf_counter() - started
    successful = [row for row in rows if row["success"]]
    scores = [float(row["score"]) for row in successful]
    full_levels = {
        level: sum(
            row["levels"].get(level, {}).get("known") == 1.0
            for row in successful
        )
        for level in ENABLED_LEVELS
    }
    run_counts = {
        level: Counter(
            row["levels"].get(level, {}).get("runs") for row in successful
        )
        for level in ENABLED_LEVELS
    }

    print("\nSummary")
    print(f"  artifact:       {wasm_path}")
    print(f"  enabled levels: {', '.join(map(str, ENABLED_LEVELS))}")
    print(f"  rng seed:       {rng_seed}")
    print(f"  workers:        {jobs}")
    print(f"  elapsed:        {elapsed:.2f}s ({args.count / elapsed:.2f} seeds/s)")
    print(f"  successful:     {len(successful)}/{args.count}")
    for level in ENABLED_LEVELS:
        print(f"  full Level {level}:   {full_levels[level]}/{args.count}")
    if scores:
        print(
            "  total score:    "
            f"mean={statistics.fmean(scores):.2f}  "
            f"min={min(scores):.2f}  max={max(scores):.2f}"
        )
        for level in ENABLED_LEVELS:
            distribution = ", ".join(
                f"{runs} runs: {amount}"
                for runs, amount in sorted(
                    run_counts[level].items(),
                    key=lambda item: (item[0] is None, item[0]),
                )
            )
            print(f"  Level {level} runs:   {distribution}")

    all_full = all(
        full_levels[level] == args.count for level in ENABLED_LEVELS
    )
    return 0 if len(successful) == args.count and all_full else 1


if __name__ == "__main__":
    raise SystemExit(main())
