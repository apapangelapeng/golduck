#!/usr/bin/env python3
"""Validate noisy Spacefiller bitmap observations through the agent API."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import multiprocessing
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from agent_simulation import agent_simulate_and_score
from analysis.level2.reverse_spacefiller import _load_probe_pattern
from golduck.rle import encode_rle, parse_rle


ROOT = Path(__file__).resolve().parents[2]
TABLE_PATH = ROOT / "analysis/level2/spacefiller2-bitmaps.json"


def secret_for_seed(seed_hex: str) -> int:
    digest = hmac.new(
        bytes.fromhex(seed_hex), b"secret_2", hashlib.sha256
    ).digest()
    return int.from_bytes(digest[:8], "little")


def secret_geometry(secret: int) -> tuple[int, ...]:
    parity_generator = random.Random(secret)
    result = []
    previous = 0
    parity = 0
    for bit in range(64):
        live = (secret >> bit) & 1
        if live and not previous:
            parity = parity_generator.randrange(2)
        result.append(0 if not live else parity + 1)
        previous = live
    return tuple(result)


def output_bitmap(rle: str, dx: int) -> int:
    pattern = parse_rle(rle, require_header=True)
    bitmap = 0
    window_x = 450 + dx
    for y, intervals in pattern.rows.items():
        if not 30 <= y < 40:
            continue
        for start, end in intervals:
            for x in range(max(start, window_x), min(end, window_x + 100)):
                bitmap |= 1 << ((y - 30) * 100 + x - window_x)
    return bitmap


def validate_seed(seed_hex: str) -> dict[str, object]:
    payload = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    expected = {
        record["geometry"]: int(record["bitmap"], 16)
        for record in payload["records"]
    }
    geometry = secret_geometry(secret_for_seed(seed_hex))
    started = agent_simulate_and_score({"action": "start", "seed": seed_hex})
    session_id = str(started["session_id"])
    distances = []
    try:
        for start in range(-2, 58, 4):
            dx = 3 * (start - 28)
            response = agent_simulate_and_score(
                {
                    "action": "run",
                    "session_id": session_id,
                    "level": 2,
                    "pattern": encode_rle(_load_probe_pattern(dx=dx)),
                    "generations": 1500,
                }
            )
            context = "".join(
                str(geometry[bit] if 0 <= bit < 64 else 0)
                for bit in range(start, start + 8)
            )
            observed = output_bitmap(str(response["output_rle"]), dx)
            distances.append((observed ^ expected[context]).bit_count())
    finally:
        agent_simulate_and_score(
            {"action": "close", "session_id": session_id}
        )
    return {
        "seed": seed_hex,
        "distances": distances,
        "total": sum(distances),
        "maximum": max(distances),
        "noisy_runs": sum(distance != 0 for distance in distances),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", nargs="+")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=args.workers, mp_context=context
    ) as executor:
        futures = [executor.submit(validate_seed, seed) for seed in args.seeds]
        for future in as_completed(futures):
            result = future.result()
            print(
                f"{result['seed']} total={result['total']} "
                f"max={result['maximum']} noisy_runs={result['noisy_runs']} "
                f"distances={result['distances']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
