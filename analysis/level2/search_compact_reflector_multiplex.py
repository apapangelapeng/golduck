#!/usr/bin/env python3
"""Test whether compact-reflector residue banks can share one Level 2 run.

The production reflector probe launches eight glider/catalyst lanes at one
residue modulo eight.  This search unions several residue banks, evolves the
exact Level 2 geometry with bgolly, and compares every decoded return against
the same residue bank evolved alone.  Matching individual transcripts is a
stronger condition than merely retaining some output population.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import itertools
import random
import subprocess
import sys
import tempfile
from collections import defaultdict
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
BASE_GENERATION = 4025

RIGHT_GLIDER = ((0, 0), (1, 0), (1, 2), (2, 0), (2, 1))
RIGHT_CATALYST5 = ((0, 1), (0, 2), (1, 0), (1, 2), (2, 1))
RIGHT_CATALYST7 = (
    (0, 2),
    (0, 3),
    (1, 1),
    (1, 3),
    (2, 1),
    (3, 0),
    (3, 1),
)
LEFT_GLIDER = ((0, 0), (0, 1), (1, 0), (1, 2), (2, 0))
LEFT_CATALYST5 = ((0, 1), (1, 0), (1, 2), (2, 1), (2, 2))
LEFT_CATALYST7 = (
    (0, 0),
    (0, 1),
    (1, 1),
    (2, 1),
    (2, 3),
    (3, 2),
    (3, 3),
)

# Complete generation-4025 return phases, relative to the marker base.
RIGHT_RETURN = ((0, 0), (2, 0), (0, 1), (1, 1), (1, 2))
LEFT_RETURN = ((0, 0), (2, 0), (1, 1), (2, 1), (1, 2))


def uses_left(residue: int) -> bool:
    return 3 <= residue <= 5


def add_shape(
    cells: set[tuple[int, int]],
    shape: tuple[tuple[int, int], ...],
    x: int,
    y: int,
) -> None:
    cells.update((x + dx, y + dy) for dx, dy in shape)


def bank_cells(residue: int, delay: int = 0) -> set[tuple[int, int]]:
    """Build one residue bank, optionally delayed along its glider paths.

    Moving the launcher backwards along its northbound diagonal and the two
    reflectors forwards along the returning diagonal preserves the collision
    location while delaying the complete return by four generations per
    cell.  The transform also separates otherwise incompatible catalyst
    banks in the initial pattern.
    """

    cells: set[tuple[int, int]] = set()
    for bit in range(residue, 64, 8):
        if uses_left(residue):
            add_shape(cells, LEFT_GLIDER, 702 + 3 * bit + delay, delay)
            add_shape(
                cells, LEFT_CATALYST5, 97 + 3 * bit - delay, 7 + delay
            )
            add_shape(
                cells, LEFT_CATALYST7, 98 + 3 * bit - delay, 13 + delay
            )
        else:
            add_shape(cells, RIGHT_GLIDER, 107 + 3 * bit - delay, delay)
            add_shape(
                cells, RIGHT_CATALYST5, 712 + 3 * bit + delay, 7 + delay
            )
            add_shape(
                cells, RIGHT_CATALYST7, 710 + 3 * bit + delay, 13 + delay
            )
    return cells


BANKS = tuple(bank_cells(residue) for residue in range(8))


def simultaneous_bank_cells(residue: int, displacement: int) -> set[tuple[int, int]]:
    """Keep every launcher simultaneous while separating right reflectors."""

    cells: set[tuple[int, int]] = set()
    for bit in range(residue, 64, 8):
        add_shape(cells, RIGHT_GLIDER, 107 + 3 * bit, 0)
        add_shape(
            cells,
            RIGHT_CATALYST5,
            712 + 3 * bit + displacement,
            7 + displacement,
        )
        add_shape(
            cells,
            RIGHT_CATALYST7,
            710 + 3 * bit + displacement,
            13 + displacement,
        )
    return cells


def contestant_pattern(
    residues: tuple[int, ...],
    delays: tuple[int, ...],
    simultaneous: bool = False,
):
    cells: set[tuple[int, int]] = set()
    for residue in residues:
        if simultaneous:
            cells.update(simultaneous_bank_cells(residue, delays[residue]))
        else:
            cells.update(bank_cells(residue, delays[residue]))
    return pattern_from_cells(cells, 1000, 200)


def combined_rle(
    residues: tuple[int, ...],
    secret: int,
    delays: tuple[int, ...],
    simultaneous: bool = False,
) -> str:
    level = Level2(secret)
    secret_x, secret_y, secret_rle = level.get_secret()
    combined = merge_placements(
        [
            (CANVAS[0], CANVAS[1], _corner_blocks(CANVAS[2], CANVAS[3])),
            (secret_x, secret_y, parse_rle(secret_rle, require_header=True)),
            (-500, -100, contestant_pattern(residues, delays, simultaneous)),
        ],
        CANVAS,
    )
    return encode_rle(combined)


def view_cells(rle: str) -> set[tuple[int, int]]:
    pattern = parse_rle(rle, require_header=True)
    left = VIEW[0] - CANVAS[0]
    top = VIEW[1] - CANVAS[1]
    right = left + VIEW[2]
    bottom = top + VIEW[3]
    cells: set[tuple[int, int]] = set()
    for y, row in pattern.rows.items():
        if not top <= y < bottom:
            continue
        for start, end in row:
            for x in range(max(start, left), min(end, right)):
                cells.add((x - left, y - top))
    return cells


def decode(
    cells: set[tuple[int, int]],
    residue: int,
    delay: int = 0,
    horizon: int = 0,
    simultaneous: bool = False,
) -> int:
    left = uses_left(residue) and not simultaneous
    shape = LEFT_RETURN if left else RIGHT_RETURN
    if simultaneous:
        base_delta = 311 + 2 * delay
        base_y = 0
    elif left:
        base_delta = 498 - 3 * delay + horizon
        base_y = horizon - delay
    else:
        base_delta = 311 + 3 * delay - horizon
        base_y = horizon - delay
    events = 0
    for lane, bit in enumerate(range(residue, 64, 8)):
        base_x = base_delta + 3 * bit
        if all((base_x + dx, base_y + dy) in cells for dx, dy in shape):
            events |= 1 << lane
    return events


def evolve(
    input_path: Path,
    output_path: Path,
    residues: tuple[int, ...],
    secret: int,
    delays: tuple[int, ...] = (0,) * 8,
    horizon: int = 0,
    simultaneous: bool = False,
) -> set[tuple[int, int]]:
    input_path.write_text(
        combined_rle(residues, secret, delays, simultaneous), encoding="ascii"
    )
    completed = subprocess.run(
        [
            str(BGOLLY),
            "-q",
            "-q",
            "-m",
            str(BASE_GENERATION + 4 * horizon),
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
    return view_cells(output_path.read_text(encoding="ascii"))


def test_secret(
    request: tuple[
        int,
        tuple[tuple[int, ...], ...],
        tuple[int, ...],
        int,
        bool,
    ],
) -> tuple[int, dict[tuple[int, ...], tuple[int, int]]]:
    secret, candidates, delays, horizon, simultaneous = request
    with tempfile.TemporaryDirectory(prefix="reflector_multiplex_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        needed_residues = sorted({residue for item in candidates for residue in item})
        expected = {
            residue: decode(
                evolve(
                    input_path,
                    output_path,
                    (residue,),
                    secret,
                    delays,
                    horizon,
                    simultaneous,
                ),
                residue,
                delays[residue],
                horizon,
                simultaneous,
            )
            for residue in needed_residues
        }
        result: dict[tuple[int, ...], tuple[int, int]] = {}
        for candidate in candidates:
            cells = evolve(
                input_path,
                output_path,
                candidate,
                secret,
                delays,
                horizon,
                simultaneous,
            )
            matched = 0
            total = 0
            for residue in candidate:
                total += 1
                if (
                    decode(
                        cells,
                        residue,
                        delays[residue],
                        horizon,
                        simultaneous,
                    )
                    == expected[residue]
                ):
                    matched += 1
            result[candidate] = (matched, total)
    return secret, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=12)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-size", type=int, default=2)
    parser.add_argument("--max-size", type=int, default=2)
    parser.add_argument(
        "--residue-set",
        help="test one comma-separated residue set instead of all combinations",
    )
    parser.add_argument(
        "--stagger-step",
        type=int,
        default=0,
        help="delay residue r by r*STEP cells and sample all at the last return",
    )
    parser.add_argument(
        "--simultaneous-reflectors",
        action="store_true",
        help="move only all-right catalyst banks; secret collisions stay simultaneous",
    )
    args = parser.parse_args()

    if args.residue_set:
        candidate = tuple(int(item) for item in args.residue_set.split(","))
        if tuple(sorted(set(candidate))) != candidate or not candidate:
            parser.error("--residue-set must be a strictly increasing subset of 0..7")
        candidates = (candidate,)
    else:
        candidates = tuple(
            candidate
            for size in range(args.min_size, args.max_size + 1)
            for candidate in itertools.combinations(range(8), size)
        )
    generator = random.Random(0xC0A5_EF1E_C70A)
    fixed = (0, (1 << 64) - 1, 0xAAAAAAAAAAAAAAAA, 0x5555555555555555)
    secrets = fixed[: args.seeds]
    if len(secrets) < args.seeds:
        secrets += tuple(generator.getrandbits(64) for _ in range(args.seeds - len(secrets)))

    delays = tuple(residue * args.stagger_step for residue in range(8))
    horizon = 0 if args.simultaneous_reflectors else max(delays)
    for residue, delay in enumerate(delays):
        cells = (
            simultaneous_bank_cells(residue, delay)
            if args.simultaneous_reflectors
            else bank_cells(residue, delay)
        )
        if (
            min(x for x, _ in cells) < 0
            or max(x for x, _ in cells) >= 1000
            or min(y for _, y in cells) < 0
            or max(y for _, y in cells) >= 200
        ):
            parser.error(
                f"staggered residue {residue} leaves the 1000x200 input rectangle"
            )

    totals: dict[tuple[int, ...], list[int]] = defaultdict(lambda: [0, 0])
    requests = tuple(
        (
            secret,
            candidates,
            delays,
            horizon,
            args.simultaneous_reflectors,
        )
        for secret in secrets
    )
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for _, result in executor.map(test_secret, requests):
            for candidate, (matched, total) in result.items():
                totals[candidate][0] += matched
                totals[candidate][1] += total

    compatible: list[tuple[int, ...]] = []
    for candidate in candidates:
        matched, total = totals[candidate]
        population = len(
            set().union(
                *(
                    simultaneous_bank_cells(r, delays[r])
                    if args.simultaneous_reflectors
                    else bank_cells(r, delays[r])
                    for r in candidate
                )
            )
        )
        status = "PASS" if matched == total else "FAIL"
        print(
            f"{status} residues={','.join(map(str, candidate)):<15} "
            f"channels={matched}/{total} cells={population}"
        )
        if matched == total:
            compatible.append(candidate)
    print(
        f"tested={len(candidates)} candidates x {len(secrets)} secrets; "
        f"compatible={len(compatible)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
