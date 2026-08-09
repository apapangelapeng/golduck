#!/usr/bin/env python3
"""Measure singleton-bit influence for one or two opposing Halfmax probes."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from golduck.level2 import Level2
from golduck.rle import encode_rle, merge_placements, parse_rle, pattern_from_cells
from golduck.sim import _corner_blocks


BGOLLY = Path("/opt/homebrew/bin/bgolly")
CANVAS = (-1800, -1800, 3600, 3600)
VIEW = (-500, 300, 1000, 200)


def load_halfmax(path: Path) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    pattern = parse_rle(path.read_text(encoding="ascii"), require_header=True)
    raw = {
        (x, y)
        for y, row in pattern.rows.items()
        for start, end in row
        for x in range(start, end)
    }
    # The canonical 65x80 pattern rotated 270 degrees expands to the left.
    left = {(y, 64 - x) for x, y in raw}
    right = {(79 - x, y) for x, y in left}
    return left, right


def contestant_cells(
    left: set[tuple[int, int]],
    right: set[tuple[int, int]],
    placements: tuple[tuple[str, int, int], ...],
) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for orientation, origin_x, origin_y in placements:
        shape = left if orientation == "L" else right
        cells.update((origin_x + x, origin_y + y) for x, y in shape)
    if not cells:
        raise ValueError("at least one placement is required")
    if (
        min(x for x, _ in cells) < 0
        or max(x for x, _ in cells) >= 1000
        or min(y for _, y in cells) < 0
        or max(y for _, y in cells) >= 200
    ):
        raise ValueError("Halfmax placement leaves the contestant rectangle")
    return cells


def input_rle(cells: set[tuple[int, int]], secret: int) -> str:
    level = Level2(secret)
    secret_x, secret_y, secret_rle = level.get_secret()
    combined = merge_placements(
        [
            (CANVAS[0], CANVAS[1], _corner_blocks(CANVAS[2], CANVAS[3])),
            (secret_x, secret_y, parse_rle(secret_rle, require_header=True)),
            (-500, -100, pattern_from_cells(cells, 1000, 200)),
        ],
        CANVAS,
    )
    return encode_rle(combined)


def view_signature(rle: str) -> tuple[int, str, tuple[int, int, int, int] | None]:
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
    if intervals:
        bbox = (
            min(start for _, start, _ in intervals),
            min(y for y, _, _ in intervals),
            max(end for _, _, end in intervals) - 1,
            max(y for y, _, _ in intervals),
        )
    else:
        bbox = None
    return population, digest, bbox


def evaluate(
    request: tuple[
        set[tuple[int, int]],
        int,
        int,
    ]
) -> tuple[int, tuple[int, str, tuple[int, int, int, int] | None]]:
    cells, secret, generations = request
    with tempfile.TemporaryDirectory(prefix="halfmax_multiplex_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        input_path.write_text(input_rle(cells, secret), encoding="ascii")
        completed = subprocess.run(
            [
                str(BGOLLY),
                "-q",
                "-q",
                "-m",
                str(generations),
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
        return secret, view_signature(output_path.read_text(encoding="ascii"))


def parse_placement(value: str) -> tuple[str, int, int]:
    try:
        orientation, x, y = value.split(":")
        if orientation not in ("L", "R"):
            raise ValueError
        return orientation, int(x), int(y)
    except ValueError as error:
        raise argparse.ArgumentTypeError("placement must be L:X:Y or R:X:Y") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shape", type=Path, default=Path("/tmp/halfmax.rle"))
    parser.add_argument("--placement", type=parse_placement, action="append")
    parser.add_argument("--generations", type=int, default=1800)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--bit-min", type=int, default=0)
    parser.add_argument("--bit-max", type=int, default=63)
    args = parser.parse_args()

    left, right = load_halfmax(args.shape)
    placements = tuple(args.placement or [("L", 398, 0)])
    cells = contestant_cells(left, right, placements)
    secrets = (0,) + tuple(1 << bit for bit in range(args.bit_min, args.bit_max + 1))
    requests = tuple((cells, secret, args.generations) for secret in secrets)
    signatures: dict[int, tuple[int, str, tuple[int, int, int, int] | None]] = {}
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for secret, signature in executor.map(evaluate, requests):
            signatures[secret] = signature

    baseline = signatures[0]
    influenced = [
        bit
        for bit in range(args.bit_min, args.bit_max + 1)
        if signatures[1 << bit] != baseline
    ]
    print(
        f"placements={placements} cells={len(cells)} generations={args.generations} "
        f"baseline={baseline}"
    )
    print("influenced=" + (",".join(map(str, influenced)) if influenced else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
