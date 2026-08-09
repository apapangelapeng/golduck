#!/usr/bin/env python3
"""Enumerate selected Wickstretcher probes over complete suffix contexts."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LEVEL3_ANALYSIS = Path(__file__).resolve().parent
if str(LEVEL3_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(LEVEL3_ANALYSIS))

from golduck.rle import encode_rle, extract_rect, parse_rle, pattern_from_cells
from search_wickstretcher_level3 import (
    DEFAULT_BGOLLY,
    VIEW,
    fnv1a64,
    glyph_cells,
    wick_cells,
    wrapped_rle,
)

DEFAULT_PROBES = (
    "north:1075:g3300",
    "mirror:1062:g3300",
    "north:1068:g3300",
    "north:1064:g3300",
)


def context_cells(value: int, nibbles: int) -> set[tuple[int, int]]:
    digits = [0] * (16 - nibbles)
    digits.extend(
        (value >> (4 * shift)) & 15
        for shift in range(nibbles - 1, -1, -1)
    )
    return set().union(
        *(
            glyph_cells(digit, -79 + 10 * index)
            for index, digit in enumerate(digits)
        )
    )


def evaluate_shard(
    request: tuple[str, int, int, int, str]
) -> tuple[str, int, list[int]]:
    probe, nibbles, start, stop, bgolly = request
    orientation, x_text, generation_text = probe.split(":")
    x_origin = int(x_text)
    generation = int(generation_text.removeprefix("g"))
    pitch = 2 * generation + 1400
    padding = generation + 650
    count = stop - start
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    width = pitch * columns
    height = pitch * rows
    world = {
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
        (width - 2, height - 2),
        (width - 1, height - 2),
        (width - 2, height - 1),
        (width - 1, height - 1),
    }
    wick = wick_cells(orientation)
    origins: list[tuple[int, int]] = []
    for tile, value in enumerate(range(start, stop)):
        origin_x = (tile % columns) * pitch + padding
        origin_y = (tile // columns) * pitch + padding
        origins.append((origin_x, origin_y))
        world.update(
            (origin_x + x, origin_y + y)
            for x, y in context_cells(value, nibbles)
        )
        world.update(
            (origin_x + x_origin - 1000 + x, origin_y + 350 + y)
            for x, y in wick
        )

    with tempfile.TemporaryDirectory(prefix="golduck_wick_contexts_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        input_path.write_text(
            wrapped_rle(pattern_from_cells(world, width, height)),
            encoding="ascii",
        )
        completed = subprocess.run(
            [
                bgolly,
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
        output = parse_rle(output_path.read_text(encoding="ascii"), require_header=True)

    signatures: list[int] = []
    for origin_x, origin_y in origins:
        view = extract_rect(
            output,
            (0, 0, width, height),
            (
                origin_x + VIEW[0],
                origin_y + VIEW[1],
                VIEW[2],
                VIEW[3],
            ),
        )
        signatures.append(fnv1a64(encode_rle(view).encode("ascii")))
    return probe, start, signatures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nibbles", type=int, default=3)
    parser.add_argument("--shard-size", type=int, default=256)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--bgolly", type=Path, default=DEFAULT_BGOLLY)
    parser.add_argument(
        "--probes",
        default=",".join(DEFAULT_PROBES),
        help="comma-separated orientation:x:generation probe names",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "analysis/level3/wick-g3300-context3-signatures.json",
    )
    args = parser.parse_args()
    if not 1 <= args.nibbles <= 4:
        parser.error("--nibbles must be between 1 and 4")
    if args.shard_size < 1:
        parser.error("--shard-size must be positive")
    probes = tuple(filter(None, args.probes.split(",")))
    context_count = 16 ** args.nibbles
    requests = tuple(
        (
            probe,
            args.nibbles,
            start,
            min(start + args.shard_size, context_count),
            str(args.bgolly),
        )
        for probe in probes
        for start in range(0, context_count, args.shard_size)
    )
    signatures = {probe: [0] * context_count for probe in probes}
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for completed, (probe, start, values) in enumerate(
            executor.map(evaluate_shard, requests), start=1
        ):
            signatures[probe][start : start + len(values)] = values
            print(
                f"evaluated {completed}/{len(requests)} {probe} "
                f"contexts {start}-{start + len(values) - 1}",
                flush=True,
            )
    generations = {
        int(probe.rsplit(":g", 1)[1]) for probe in probes
    }
    args.output.write_text(
        json.dumps(
            {
                "context_nibbles": args.nibbles,
                "generation": next(iter(generations)) if len(generations) == 1 else None,
                "signatures": signatures,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
