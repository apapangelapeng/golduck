#!/usr/bin/env python3
"""Search northbound Wickstretcher-1 probes for the Level 3 edge glyph.

Every candidate is checked against all 256 combinations of the final glyph
and its left neighbour.  Experiments are tiled farther apart than a Life
light cone, so one bgolly invocation evaluates all contexts for a candidate.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import itertools
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from golduck.level3 import _FONT_ROWS
from golduck.rle import (
    encode_rle,
    extract_rect,
    parse_rle,
    pattern_from_cells,
)

PATTERN = ROOT / "analysis/level3/wickstretcher1.rle"
DEFAULT_BGOLLY = Path("/opt/homebrew/bin/bgolly")
VIEW = (-500, -100, 1000, 200)
MASK64 = (1 << 64) - 1


@dataclass(frozen=True, order=True)
class Candidate:
    orientation: str
    x: int
    generation: int

    @property
    def name(self) -> str:
        return f"{self.orientation}:{self.x}:g{self.generation}"


def cells(pattern) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y, intervals in pattern.rows.items()
        for left, right in intervals
        for x in range(left, right)
    }


def wick_cells(orientation: str) -> set[tuple[int, int]]:
    source = cells(parse_rle(PATTERN.read_text(encoding="ascii")))
    if orientation == "north":
        return {(y, 48 - x) for x, y in source}
    if orientation == "mirror":
        return {(15 - y, 48 - x) for x, y in source}
    raise ValueError(f"unknown orientation {orientation!r}")


def glyph_cells(digit: int, x_origin: int) -> set[tuple[int, int]]:
    return {
        (x_origin + x, -400 + y)
        for y, row in enumerate(_FONT_ROWS[f"{digit:x}"])
        for x, value in enumerate(row)
        if value == "#"
    }


ZERO_PREFIX = set().union(
    *(glyph_cells(0, -79 + 10 * index) for index in range(14))
)
CONTEXTS = tuple(
    ZERO_PREFIX | glyph_cells(left, 61) | glyph_cells(digit, 71)
    for left in range(16)
    for digit in range(16)
)


def wrapped_rle(pattern) -> str:
    """Wrap at token boundaries; bgolly rejects a split count token."""
    encoded = encode_rle(pattern)
    header, body = encoded.split("\n", 1)
    tokens = re.findall(r"\d*[bo$!]", body)
    lines: list[str] = []
    line = ""
    for token in tokens:
        if line and len(line) + len(token) > 70:
            lines.append(line)
            line = ""
        line += token
    if line:
        lines.append(line)
    return header + "\n" + "\n".join(lines) + "\n"


def fnv1a64(data: bytes) -> int:
    value = 14695981039346656037
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & MASK64
    return value


def evaluate_candidate(
    request: tuple[Candidate, str],
) -> tuple[str, list[int]]:
    candidate, bgolly_text = request
    generation = candidate.generation
    pitch = 2 * generation + 1400
    padding = generation + 650
    columns = 16
    rows = math.ceil(len(CONTEXTS) / columns)
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
    wick = wick_cells(candidate.orientation)
    origins: list[tuple[int, int]] = []
    for index, context in enumerate(CONTEXTS):
        origin_x = (index % columns) * pitch + padding
        origin_y = (index // columns) * pitch + padding
        origins.append((origin_x, origin_y))
        world.update((origin_x + x, origin_y + y) for x, y in context)
        world.update(
            (
                origin_x + candidate.x - 1000 + x,
                origin_y + 350 + y,
            )
            for x, y in wick
        )

    with tempfile.TemporaryDirectory(prefix="golduck_wick_search_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        input_path.write_text(
            wrapped_rle(pattern_from_cells(world, width, height)),
            encoding="ascii",
        )
        completed = subprocess.run(
            [
                bgolly_text,
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
    return candidate.name, signatures


def cross_digit_pairs() -> set[tuple[int, int]]:
    states = tuple((left, digit) for left in range(16) for digit in range(16))
    return {
        (first, second)
        for first in range(len(states))
        for second in range(first + 1, len(states))
        if states[first][1] != states[second][1]
    }


def remaining_pairs(
    chosen: tuple[str, ...],
    signatures: dict[str, list[int]],
    pairs: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    return {
        (first, second)
        for first, second in pairs
        if all(
            signatures[name][first] == signatures[name][second]
            for name in chosen
        )
    }


def report(signatures: dict[str, list[int]]) -> None:
    pairs = cross_digit_pairs()
    separations = {
        name: {
            pair
            for pair in pairs
            if values[pair[0]] != values[pair[1]]
        }
        for name, values in signatures.items()
    }

    remaining = set(pairs)
    greedy: list[str] = []
    while remaining and len(greedy) < 10:
        name = max(
            (name for name in signatures if name not in greedy),
            key=lambda item: len(remaining & separations[item]),
        )
        gain = len(remaining & separations[name])
        greedy.append(name)
        remaining -= separations[name]
        print(
            f"greedy {len(greedy)}: {name} gain={gain} "
            f"remaining_cross_digit_pairs={len(remaining)}"
        )
        if gain == 0:
            break

    normal = sorted(name for name in signatures if name.startswith("north:"))
    mirror = sorted(name for name in signatures if name.startswith("mirror:"))
    four_run: list[tuple[str, ...]] = []
    for normal_three in itertools.combinations(normal, 3):
        resolved: set[tuple[int, int]] = set()
        for name in normal_three:
            resolved |= separations[name]
        unresolved = pairs - resolved
        for mirror_one in mirror:
            if unresolved <= separations[mirror_one]:
                four_run.append(normal_three + (mirror_one,))
    print(f"exact normal+normal+normal+mirror combinations: {len(four_run)}")
    for solution in four_run[:25]:
        print("  " + " ".join(solution))

    for count in range(1, len(greedy) + 1):
        chosen = tuple(greedy[:count])
        groups: dict[tuple[int, ...], set[int]] = defaultdict(set)
        for index in range(256):
            groups[tuple(signatures[name][index] for name in chosen)].add(index & 15)
        forced_counts: list[int] = []
        for index in range(256):
            digits = groups[tuple(signatures[name][index] for name in chosen)]
            first = next(iter(digits))
            common = 15
            for digit in digits:
                common &= ~(first ^ digit) & 15
            forced_counts.append(common.bit_count())
        print(
            f"prefix {count}: output_classes={len(groups)} "
            f"ambiguous_classes={sum(len(group) > 1 for group in groups.values())} "
            f"min_forced_bits={min(forced_counts)} "
            f"mean_forced_bits={sum(forced_counts) / len(forced_counts):.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=int, default=3300)
    parser.add_argument("--x-min", type=int, default=1062)
    parser.add_argument("--x-max", type=int, default=1079)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--bgolly", type=Path, default=DEFAULT_BGOLLY)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    candidates = tuple(
        Candidate(orientation, x, args.generation)
        for orientation in ("north", "mirror")
        for x in range(args.x_min, args.x_max + 1)
    )
    requests = tuple((candidate, str(args.bgolly)) for candidate in candidates)
    signatures: dict[str, list[int]] = {}
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for index, (name, values) in enumerate(
            executor.map(evaluate_candidate, requests), start=1
        ):
            signatures[name] = values
            print(f"evaluated {index}/{len(candidates)} {name}", flush=True)

    report(signatures)
    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "generation": args.generation,
                    "signatures": signatures,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
