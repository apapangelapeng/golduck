#!/usr/bin/env python3
"""Search for two Max151 copies that retain a return in one Level 2 run.

Two copies at the same vertical phase annihilate for useful horizontal
separations.  This scans vertical phase offsets with exact Life evolution and
reports every composite whose generation-1326 output still reaches the
Level 2 viewing rectangle.  A surviving composite is only a multiplexing
candidate; its two local secret channels still need exhaustive calibration.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from golduck.rle import encode_rle, merge_placements, parse_rle, pattern_from_cells
from golduck.level2 import Level2
from golduck.sim import _corner_blocks


SOURCE = ROOT / "solution/max151_adaptive8.c"
BGOLLY = Path("/opt/homebrew/bin/bgolly")
CANVAS = (-1800, -1800, 3600, 3600)
VIEW = (-500, 300, 1000, 200)
GENERATION = 1326


def _pattern_cells() -> set[tuple[int, int]]:
    source = SOURCE.read_text(encoding="ascii")
    match = re.search(
        r"static const char\* SF2PAT\[SF2PROBES\] = \{\s*"
        r"(\"(?:[^\"\\]|\\.)*\")",
        source,
        re.S,
    )
    if match is None:
        raise ValueError(f"could not extract SF2PAT[0] from {SOURCE}")
    pattern = parse_rle(ast.literal_eval(match.group(1)), require_header=True)
    return {
        (x, y)
        for y, intervals in pattern.rows.items()
        for start, end in intervals
        for x in range(start, end)
    }


def _candidate_pattern(bit_delta: int, y_delta: int):
    base = _pattern_cells()
    y_pad = max(0, -y_delta)
    cells = {(x, y + y_pad) for x, y in base}
    cells.update((x + 3 * bit_delta, y + y_pad + y_delta) for x, y in base)
    if not cells or min(x for x, _ in cells) < 0 or max(x for x, _ in cells) >= 1000:
        raise ValueError("horizontal placement leaves the contestant rectangle")
    if min(y for _, y in cells) < 0 or max(y for _, y in cells) >= 200:
        raise ValueError("vertical placement leaves the contestant rectangle")
    return pattern_from_cells(cells, 1000, 200)


def _candidate_rle(bit_delta: int, y_delta: int, secret: int = 0) -> str:
    contestant = _candidate_pattern(bit_delta, y_delta)
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


def _view_signature(rle: str) -> tuple[int, str]:
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
        for start, end in row:
            clipped_start = max(start, left)
            clipped_end = min(end, right)
            if clipped_start < clipped_end:
                intervals.append((y - top, clipped_start - left, clipped_end - left))
                population += clipped_end - clipped_start
    digest = hashlib.sha256(repr(intervals).encode("ascii")).hexdigest()[:16]
    return population, digest


def _scan_batch(candidates: list[tuple[int, int]]) -> list[tuple[int, int, int, str]]:
    survivors: list[tuple[int, int, int, str]] = []
    with tempfile.TemporaryDirectory(prefix="max151_multiplex_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for bit_delta, y_delta in candidates:
            input_path.write_text(_candidate_rle(bit_delta, y_delta), encoding="ascii")
            completed = subprocess.run(
                [
                    str(BGOLLY),
                    "-q",
                    "-q",
                    "-m",
                    str(GENERATION),
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
            population, digest = _view_signature(
                output_path.read_text(encoding="ascii")
            )
            if population:
                survivors.append((bit_delta, y_delta, population, digest))
    return survivors


def _influence_batch(
    request: tuple[list[tuple[int, int]], int, int],
) -> list[tuple[int, int, int, str, tuple[int, ...]]]:
    candidates, bit_min, bit_max = request
    results: list[tuple[int, int, int, str, tuple[int, ...]]] = []
    with tempfile.TemporaryDirectory(prefix="max151_influence_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"

        def evolve(secret: int) -> tuple[int, str]:
            input_path.write_text(
                _candidate_rle(bit_delta, y_delta, secret), encoding="ascii"
            )
            completed = subprocess.run(
                [
                    str(BGOLLY),
                    "-q",
                    "-q",
                    "-m",
                    str(GENERATION),
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
            return _view_signature(output_path.read_text(encoding="ascii"))

        for bit_delta, y_delta in candidates:
            baseline_population, baseline_digest = evolve(0)
            influenced = tuple(
                bit
                for bit in range(bit_min, bit_max + 1)
                if evolve(1 << bit) != (baseline_population, baseline_digest)
            )
            results.append(
                (
                    bit_delta,
                    y_delta,
                    baseline_population,
                    baseline_digest,
                    influenced,
                )
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit-min", type=int, default=6)
    parser.add_argument("--bit-max", type=int, default=24)
    parser.add_argument("--y-min", type=int, default=-48)
    parser.add_argument("--y-max", type=int, default=48)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--test-bits",
        action="store_true",
        help="also compare every singleton-bit secret for surviving composites",
    )
    parser.add_argument("--test-bit-min", type=int, default=0)
    parser.add_argument("--test-bit-max", type=int, default=63)
    args = parser.parse_args()

    candidates = [
        (bit_delta, y_delta)
        for bit_delta in range(args.bit_min, args.bit_max + 1)
        for y_delta in range(args.y_min, args.y_max + 1)
    ]
    batches = [candidates[index :: args.workers] for index in range(args.workers)]
    survivors: list[tuple[int, int, int, str]] = []
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for result in executor.map(_scan_batch, batches):
            survivors.extend(result)
    survivors.sort(key=lambda row: (-row[2], row[0], row[1]))
    for bit_delta, y_delta, population, digest in survivors:
        print(
            f"bit_delta={bit_delta:2d} y_delta={y_delta:3d} "
            f"view_population={population:6d} signature={digest}"
        )
    print(f"tested={len(candidates)} survivors={len(survivors)}")
    if args.test_bits and survivors:
        survivor_pairs = [(row[0], row[1]) for row in survivors]
        influence_batches = [
            (
                survivor_pairs[index :: args.workers],
                args.test_bit_min,
                args.test_bit_max,
            )
            for index in range(args.workers)
        ]
        influence: list[tuple[int, int, int, str, tuple[int, ...]]] = []
        with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
            for result in executor.map(_influence_batch, influence_batches):
                influence.extend(result)
        influence.sort()
        for bit_delta, y_delta, population, digest, bits in influence:
            bit_text = ",".join(map(str, bits)) if bits else "none"
            print(
                f"influence bit_delta={bit_delta:2d} y_delta={y_delta:3d} "
                f"baseline={population}:{digest} singleton_bits={bit_text}"
            )


if __name__ == "__main__":
    main()
