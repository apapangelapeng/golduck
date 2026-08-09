#!/usr/bin/env python3
"""Estimate full-view Max151 observation entropy across generations."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import math
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from golduck.level2 import Level2
from golduck.rle import encode_rle, merge_placements, parse_rle, pattern_from_cells
from golduck.sim import _corner_blocks


SOURCE = ROOT / "solution/max151_adaptive8.c"
BGOLLY = Path("/opt/homebrew/bin/bgolly")
CANVAS = (-1800, -1800, 3600, 3600)
VIEW = (-500, 300, 1000, 200)


def max151_cells() -> set[tuple[int, int]]:
    source = SOURCE.read_text(encoding="ascii")
    match = re.search(
        r"static const char\* SF2PAT\[SF2PROBES\] = \{\s*"
        r"(\"(?:[^\"\\]|\\.)*\")",
        source,
        re.S,
    )
    if match is None:
        raise ValueError(f"could not extract Max151 from {SOURCE}")
    pattern = parse_rle(ast.literal_eval(match.group(1)), require_header=True)
    return {
        (x, y)
        for y, row in pattern.rows.items()
        for start, end in row
        for x in range(start, end)
    }


BASE_CELLS = max151_cells()


def input_rle(secret: int, start: int, offsets: tuple[int, ...]) -> str:
    contestant = pattern_from_cells(
        {
            (x + 3 * (start + offset), y)
            for offset in offsets
            for x, y in BASE_CELLS
        },
        1000,
        200,
    )
    level = Level2(secret)
    secret_x, secret_y, secret_rle = level.get_secret()
    combined = merge_placements(
        [
            (CANVAS[0], CANVAS[1], _corner_blocks(CANVAS[2], CANVAS[3])),
            (secret_x, secret_y, parse_rle(secret_rle, require_header=True)),
            (-500, -100, contestant),
        ],
        CANVAS,
    )
    return encode_rle(combined)


def signature(rle: str) -> tuple[int, str]:
    pattern = parse_rle(rle, require_header=True)
    left = VIEW[0] - CANVAS[0]
    top = VIEW[1] - CANVAS[1]
    right = left + VIEW[2]
    bottom = top + VIEW[3]
    intervals: list[tuple[int, int, int]] = []
    population = 0
    for y, row in pattern.rows.items():
        if not top <= y < bottom:
            continue
        for begin, end in row:
            clipped_begin = max(begin, left)
            clipped_end = min(end, right)
            if clipped_begin < clipped_end:
                intervals.append(
                    (y - top, clipped_begin - left, clipped_end - left)
                )
                population += clipped_end - clipped_begin
    digest = hashlib.sha256(repr(intervals).encode("ascii")).hexdigest()[:24]
    return population, digest


def run_batch(
    request: tuple[int, tuple[int, ...], int, tuple[int, ...]]
) -> tuple[int, tuple[tuple[int, str], ...]]:
    generation, secrets, start, offsets = request
    results: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory(prefix="max151_generation_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for secret in secrets:
            input_path.write_text(
                input_rle(secret, start, offsets), encoding="ascii"
            )
            completed = subprocess.run(
                [
                    str(BGOLLY),
                    "-q",
                    "-q",
                    "-m",
                    str(generation),
                    "-o",
                    str(output_path),
                    str(input_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "bgolly failed")
            results.append(signature(output_path.read_text(encoding="ascii")))
    return generation, tuple(results)


def parse_generations(value: str) -> tuple[int, ...]:
    if ":" not in value:
        return tuple(int(item) for item in value.split(","))
    parts = tuple(int(item) for item in value.split(":"))
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError("range must be START:STOP[:STEP]")
    start, stop = parts[:2]
    step = parts[2] if len(parts) == 3 else 1
    return tuple(range(start, stop + 1, step))


def parse_offsets(value: str) -> tuple[int, ...]:
    offsets = tuple(int(item) for item in value.split(","))
    if not offsets or tuple(sorted(set(offsets))) != offsets:
        raise argparse.ArgumentTypeError("offsets must be strictly increasing")
    return offsets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generations", type=parse_generations, default=(1326,))
    parser.add_argument("--samples", type=int, default=256)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x151)
    parser.add_argument("--start", type=int, default=26)
    parser.add_argument("--offsets", type=parse_offsets, default=(0,))
    parser.add_argument(
        "--singletons",
        action="store_true",
        help="replace random samples with zero and all 64 singleton-bit secrets",
    )
    args = parser.parse_args()

    generator = random.Random(args.seed)
    secrets = (
        (0,) + tuple(1 << bit for bit in range(64))
        if args.singletons
        else tuple(generator.getrandbits(64) for _ in range(args.samples))
    )
    sample_count = len(secrets)
    requests = [
        (
            generation,
            secrets[worker :: args.workers],
            args.start,
            args.offsets,
        )
        for generation in args.generations
        for worker in range(args.workers)
    ]
    grouped: dict[int, list[tuple[int, str]]] = {
        generation: [] for generation in args.generations
    }
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for generation, results in executor.map(run_batch, requests):
            grouped[generation].extend(results)

    for generation in args.generations:
        counts = Counter(grouped[generation])
        entropy = -sum(
            count / sample_count * math.log2(count / sample_count)
            for count in counts.values()
        )
        largest = max(counts.values())
        empty = sum(count for (population, _), count in counts.items() if not population)
        print(
            f"generation={generation} offsets={args.offsets} "
            f"unique={len(counts)}/{sample_count} "
            f"sample_entropy={entropy:.4f} largest_bucket={largest} empty={empty}"
        )
        if args.singletons:
            grouped_secret_order = tuple(
                secret
                for worker in range(args.workers)
                for secret in secrets[worker :: args.workers]
            )
            by_secret = dict(zip(grouped_secret_order, grouped[generation]))
            baseline = by_secret[0]
            influenced = [
                bit
                for bit in range(64)
                if by_secret[1 << bit] != baseline
            ]
            print("influenced=" + ",".join(map(str, influenced)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
