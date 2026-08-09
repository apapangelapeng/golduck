#!/usr/bin/env python3
"""Search vertical Level 2 spaceship fleets with the agent simulation API.

The production Level 2 probe launches northbound spaceships from the
contestant rectangle, through the secret strip, and reads collision products
that return south into the viewing rectangle.  This script keeps that exact
geometry and compares several vertically separated waves in one stateful
agent session per seed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_simulation import agent_simulate_and_score
from golduck.rle import encode_rle, parse_rle, pattern_from_cells


# Eight-cell predecessor used by explore8.  Coordinates are relative to the
# 1000 x 200 contestant rectangle, whose absolute origin is (-500, -100).
COMPACT_LWSS_NORTH = (
    (0, 1),
    (1, 0),
    (2, 0),
    (2, 4),
    (3, 0),
    (3, 1),
    (3, 2),
    (3, 3),
)


@dataclass(frozen=True)
class Candidate:
    name: str
    row_offsets: tuple[int, ...]
    generations: int = 2600


@dataclass(frozen=True)
class PackedWave:
    recipe: int
    residue: int
    row_offset: int


@dataclass(frozen=True)
class PackedCandidate:
    name: str
    waves: tuple[PackedWave, ...]
    generations: int = 2600


def add_shape(
    cells: set[tuple[int, int]],
    shape: Iterable[tuple[int, int]],
    x: int,
    y: int,
) -> None:
    cells.update((x + dx, y + dy) for dx, dy in shape)


def build_fleet(
    candidate: Candidate,
    *,
    bit: int = 32,
    recipe_offset: int = 0,
    base_y: int = 195,
) -> str:
    """Build several waves aimed at one secret bit center.

    ``row_offsets`` are distances north of the normal y=195 launch row.
    Keeping the test to one lane makes it possible to distinguish temporal
    wave interactions from cross-lane interference.
    """

    cells: set[tuple[int, int]] = set()
    x = 404 + 3 * bit + recipe_offset
    for offset in candidate.row_offsets:
        add_shape(cells, COMPACT_LWSS_NORTH, x, base_y - offset)
    return encode_rle(pattern_from_cells(cells, 1000, 200))


RECIPE_OFFSETS = (0, 2, -1, -3)
RECIPE_LAUNCH_Y = (195, 194, 194, 195)
RECIPE_RETURN_DX = (1, 3, 0, -2)
RECIPE_RETURN_Y = (105, 106, 106, 105)
LWSS_SOUTH = (
    (0, 1),
    (0, 2),
    (0, 3),
    (0, 4),
    (1, 0),
    (1, 4),
    (2, 4),
    (3, 0),
    (3, 3),
)
LWSS_SOUTH_MIRROR = tuple((3 - x, y) for x, y in LWSS_SOUTH)


def bits_for_residue(residue: int) -> range:
    first_bit = 1 if residue == 3 else 2 + residue
    return range(first_bit, 62, 4)


def build_packed_fleet(candidate: PackedCandidate) -> str:
    cells: set[tuple[int, int]] = set()
    for wave in candidate.waves:
        for bit in bits_for_residue(wave.residue):
            add_shape(
                cells,
                COMPACT_LWSS_NORTH,
                404 + 3 * bit + RECIPE_OFFSETS[wave.recipe],
                RECIPE_LAUNCH_Y[wave.recipe] - wave.row_offset,
            )
    return encode_rle(pattern_from_cells(cells, 1000, 200))


def build_double_fleet(
    residue: int, *, step: int = 9, gap: int = 24
) -> str:
    """Build two waves with enough horizontal space for independent returns."""

    cells: set[tuple[int, int]] = set()
    for bit in range(residue, 64, step):
        x = 404 + 3 * bit
        add_shape(cells, COMPACT_LWSS_NORTH, x, 195)
        add_shape(cells, COMPACT_LWSS_NORTH, x, 195 - gap)
    return encode_rle(pattern_from_cells(cells, 1000, 200))


def decode_double_fleet(
    cells: set[tuple[int, int]],
    residue: int,
    *,
    step: int = 9,
    generations: int = 2496,
) -> int:
    """Decode the gap-24 two-wave return at its canonical LWSS phase."""

    # At generation 2496 the newly discovered ship first appears completely
    # in the viewing rectangle at x = 407 + 3*bit, y = 0.  It advances south
    # one cell every two generations on average; only the phase-0 generation
    # is used by the optimizer and final decoder.
    if generations != 2496:
        raise ValueError("double-fleet decoder is calibrated for generation 2496")
    events = 0
    for lane, bit in enumerate(range(residue, 64, step)):
        base_x = 407 + 3 * bit
        if all((base_x + dx, dy) in cells for dx, dy in LWSS_SOUTH_MIRROR):
            events |= 1 << lane
    return events


def decode_wave(cells: set[tuple[int, int]], wave: PackedWave) -> int:
    events = 0
    for lane, bit in enumerate(bits_for_residue(wave.residue)):
        base_x = 404 + 3 * bit + RECIPE_RETURN_DX[wave.recipe]
        base_y = RECIPE_RETURN_Y[wave.recipe] + wave.row_offset
        if all((base_x + dx, base_y + dy) in cells for dx, dy in LWSS_SOUTH):
            events |= 1 << lane
    return events


def packed_candidates() -> list[PackedCandidate]:
    candidates = [
        PackedCandidate(
            f"baseline_r{residue}",
            (PackedWave(0, residue, 0),),
        )
        for residue in range(4)
    ]
    for gap in (0, 8, 16, 24, 32, 40):
        candidates.append(
            PackedCandidate(
                f"pair_02_gap_{gap}",
                (PackedWave(0, 0, 0), PackedWave(0, 2, gap)),
            )
        )
        candidates.append(
            PackedCandidate(
                f"pair_13_gap_{gap}",
                (PackedWave(0, 1, 0), PackedWave(0, 3, gap)),
            )
        )
    return candidates


def rle_cells(rle: str) -> set[tuple[int, int]]:
    pattern = parse_rle(rle, require_header=True)
    cells: set[tuple[int, int]] = set()
    for y, intervals in pattern.rows.items():
        for start, end in intervals:
            cells.update((x, y) for x in range(start, end))
    return cells


def secret_for_seed(seed_hex: str, level: int = 2) -> int:
    seed = bytes.fromhex(seed_hex)
    digest = hmac.new(seed, f"secret_{level}".encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "little")


def local_bits(secret: int, center: int = 32, radius: int = 5) -> str:
    return "".join(
        str((secret >> bit) & 1)
        for bit in range(center - radius, center + radius + 1)
    )


def signature(cells: set[tuple[int, int]], center_x: int = 501) -> str:
    """Hash a generous lane-local patch in the returned viewing rectangle."""

    local = sorted((x - center_x, y) for x, y in cells if abs(x - center_x) <= 80)
    return hashlib.sha256(repr(local).encode()).hexdigest()[:16]


def bbox(cells: set[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    if not cells:
        return None
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    return min(xs), min(ys), max(xs), max(ys)


def default_candidates() -> list[Candidate]:
    candidates = [Candidate("single", (0,))]
    candidates.extend(
        Candidate(f"two_gap_{gap}", (0, gap))
        for gap in (4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64)
    )
    candidates.extend(
        Candidate(f"three_gap_{gap}", (0, gap, 2 * gap))
        for gap in (8, 16, 24, 32)
    )
    return candidates


def run_seed(seed_hex: str, candidates: list[Candidate]) -> list[dict[str, object]]:
    started = agent_simulate_and_score({"action": "start", "seed": seed_hex})
    session_id = str(started["session_id"])
    rows: list[dict[str, object]] = []
    try:
        for candidate in candidates:
            response = agent_simulate_and_score(
                {
                    "action": "run",
                    "session_id": session_id,
                    "level": 2,
                    "pattern": build_fleet(candidate),
                    "generations": candidate.generations,
                }
            )
            cells = rle_cells(str(response["output_rle"]))
            rows.append(
                {
                    "seed": seed_hex,
                    "secret_window": local_bits(secret_for_seed(seed_hex)),
                    "candidate": candidate.name,
                    "rows": len(candidate.row_offsets),
                    "offsets": candidate.row_offsets,
                    "generations": candidate.generations,
                    "input_cells": response["input_cell_count"],
                    "output_cells": len(cells),
                    "bbox": bbox(cells),
                    "signature": signature(cells),
                    "performance_score": response["score"],
                }
            )
    finally:
        agent_simulate_and_score({"action": "close", "session_id": session_id})
    return rows


def run_packed_seed(
    seed_hex: str, candidates: list[PackedCandidate]
) -> list[dict[str, object]]:
    started = agent_simulate_and_score({"action": "start", "seed": seed_hex})
    session_id = str(started["session_id"])
    rows: list[dict[str, object]] = []
    try:
        for candidate in candidates:
            response = agent_simulate_and_score(
                {
                    "action": "run",
                    "session_id": session_id,
                    "level": 2,
                    "pattern": build_packed_fleet(candidate),
                    "generations": candidate.generations,
                }
            )
            cells = rle_cells(str(response["output_rle"]))
            events = [decode_wave(cells, wave) for wave in candidate.waves]
            rows.append(
                {
                    "seed": seed_hex,
                    "candidate": candidate.name,
                    "waves": [
                        {
                            "recipe": wave.recipe,
                            "residue": wave.residue,
                            "row_offset": wave.row_offset,
                        }
                        for wave in candidate.waves
                    ],
                    "events": events,
                    "input_cells": response["input_cell_count"],
                    "output_cells": len(cells),
                    "bbox": bbox(cells),
                    "performance_score": response["score"],
                }
            )
    finally:
        agent_simulate_and_score({"action": "close", "session_id": session_id})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed",
        action="append",
        dest="seeds",
        help="128-bit hex seed; repeat for more seeds",
    )
    parser.add_argument("--json", type=Path, help="write full JSON results")
    parser.add_argument(
        "--mode",
        choices=("waves", "packing"),
        default="waves",
        help="scan repeated same-lane waves or packed residue rows",
    )
    args = parser.parse_args()

    seeds = args.seeds or ["00" * 16]
    if args.mode == "packing":
        candidates = packed_candidates()
        results = [
            row for seed in seeds for row in run_packed_seed(seed, candidates)
        ]
        for row in results:
            event_text = ",".join(f"0x{event:04x}" for event in row["events"])
            print(
                f"{row['seed'][:8]} {row['candidate']:<16} "
                f"in={row['input_cells']:>3} out={row['output_cells']:>4} "
                f"events={event_text}"
            )
        if args.json:
            args.json.write_text(
                json.dumps(results, indent=2) + "\n", encoding="utf-8"
            )
        return 0

    candidates = default_candidates()
    results = [row for seed in seeds for row in run_seed(seed, candidates)]

    for row in results:
        print(
            f"{row['seed'][:8]} {row['candidate']:<14} "
            f"in={row['input_cells']:>3} out={row['output_cells']:>4} "
            f"bbox={row['bbox']!s:<22} sig={row['signature']}"
        )
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
