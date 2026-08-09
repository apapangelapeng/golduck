#!/usr/bin/env python3
"""Build isolated Level 3 Spacefiller collision tiles for bgolly.

Each tile uses Level 3's real four-glyph font and the exact contestant probe.
The large separation makes every tile causally independent for ``generation``
steps, while allowing bgolly/Hashlife to share the common evolution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from golduck.level3 import _FONT_ROWS
from golduck.rle import encode_rle, parse_rle, pattern_from_cells


SHAPE_PATH = ROOT / "analysis/level2/spacefiller-2-single-copy.idea-lab.json"

SPACEFILLER1_RLE = """x = 29, y = 43, rule = B3/S23
5bobo$4bo2bo$3b2o$2bo$b4o$o4bo$o2bo$o2bo$bo9b3o3b3o$2b4obo2bo2bo3bo2bo$
3bo3bo5bo3bo$4bo8bo3bo$4bobo6bo3bo2$3b3o5b3o3b3o$3b2o7bo5bo$3b3o6b7o$
11bo7bo$4bobo3b11o$3bo2bo2bo12b2o$3bo5b12o3bo$3bo3bo13bo3bo$
4bo3b12o5bo$5b2o12bo2bo2bo$8b11o3bobo$9bo7bo$10b7o6b3o$
10bo5bo7b2o$9b3o3b3o5b3o2$11bo3bo6bobo$11bo3bo8bo$11bo3bo5bo3bo$
8bo2bo3bo2bo2bob4o$9b3o3b3o9bo$25bo2bo$25bo2bo$23bo4bo$24b4o$26bo$
24b2o$21bo2bo$21bobo!
"""

SMALL_SPACEFILLER_RLE = """x = 49, y = 26, rule = B3/S23
20b3o3b3o$19bo2bo3bo2bo$4o18bo3bo18b4o$o3bo17bo3bo17bo3bo$
o8bo12bo3bo12bo8bo$bo2bo2b2o2bo25bo2b2o2bo2bo$6bo5bo7b3o3b3o7bo5bo$
6bo5bo8bo5bo8bo5bo$6bo5bo8b7o8bo5bo$
bo2bo2b2o2bo2b2o4bo7bo4b2o2bo2b2o2bo2bo$
o8bo3b2o4b11o4b2o3bo8bo$o3bo9b2o17b2o9bo3bo$
4o11b19o11b4o$16bobo11bobo$19b11o$19bo9bo$20b9o$24bo$
20b3o3b3o$22bo3bo2$21b3ob3o$21b3ob3o$20bobo2bobo2bo$
20b3o3b3o$21bo5bo!
"""


def cells_from_rle(rle: str) -> set[tuple[int, int]]:
    pattern = parse_rle(rle, require_header=True)
    return {
        (x, y)
        for y, intervals in pattern.rows.items()
        for left, right in intervals
        for x in range(left, right)
    }


def glyph_cells(digit: int, origin_x: int, origin_y: int) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for y, row in enumerate(_FONT_ROWS[f"{digit:x}"]):
        for x, cell in enumerate(row):
            if cell == "#":
                result.add((origin_x + x, origin_y + y))
    return result


def probe_cells(
    dx: int, dy: int, transform: str, shape: str
) -> set[tuple[int, int]]:
    if shape == "spacefiller1":
        source = cells_from_rle(SPACEFILLER1_RLE)
    elif shape == "small-spacefiller":
        source = cells_from_rle(SMALL_SPACEFILLER_RLE)
    else:
        placement = json.loads(SHAPE_PATH.read_text(encoding="utf-8"))["placements"][0]
        source = {tuple(cell) for cell in placement["cells"]}
    source_width = max(x for x, _ in source) + 1
    source_height = max(y for _, y in source) + 1
    if transform == "mirror-x":
        transformed = {(source_width - 1 - x, y) for x, y in source}
    elif transform == "rotate-90":
        transformed = {(source_height - 1 - y, x) for x, y in source}
    elif transform == "rotate-270":
        transformed = {(y, source_width - 1 - x) for x, y in source}
    else:
        transformed = source
    width = max(x for x, _ in transformed) + 1
    height = max(y for _, y in transformed) + 1
    # These are absolute Level 3 coordinates, centered at (0, 400).
    return {
        (x - width // 2 + dx, 400 + y - height // 2 + dy)
        for x, y in transformed
    }


def context_digits(
    index: int, pair_only: bool, pair_slots: tuple[int, int]
) -> tuple[int, int, int, int]:
    if pair_only:
        result = [0, 0, 0, 0]
        result[pair_slots[0]] = (index >> 4) & 15
        result[pair_slots[1]] = index & 15
        return tuple(result)  # type: ignore[return-value]
    return (
        (index >> 12) & 15,
        (index >> 8) & 15,
        (index >> 4) & 15,
        index & 15,
    )


def wrapped_rle(cells: set[tuple[int, int]], side: int) -> str:
    """Encode with line breaks only between complete RLE tokens."""

    encoded = encode_rle(pattern_from_cells(cells, side, side))
    header, body = encoded.split("\n", 1)
    body = body.strip()
    lines: list[str] = []
    current: list[str] = []
    for character in body:
        current.append(character)
        if character in "bo$!" and len(current) >= 70:
            lines.append("".join(current))
            current.clear()
    if current:
        lines.append("".join(current))
    return header + "\n" + "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--grid", type=int, default=16)
    parser.add_argument("--offset", type=lambda value: int(value, 0), default=0)
    parser.add_argument("--pair-only", action="store_true")
    parser.add_argument(
        "--pair-slots",
        type=lambda value: tuple(map(int, value.split(","))),
        default=(1, 2),
        help="two comma-separated context positions varied by --pair-only",
    )
    parser.add_argument("--probe-dx", type=int, default=0)
    parser.add_argument("--probe-dy", type=int, default=0)
    parser.add_argument(
        "--shape",
        choices=("spacefiller2", "spacefiller1", "small-spacefiller"),
        default="spacefiller2",
    )
    parser.add_argument(
        "--transform",
        choices=("identity", "mirror-x", "rotate-90", "rotate-270"),
        default="identity",
    )
    args = parser.parse_args()

    if args.generation < 0:
        parser.error("generation must be nonnegative")
    if (
        len(args.pair_slots) != 2
        or len(set(args.pair_slots)) != 2
        or any(slot not in range(4) for slot in args.pair_slots)
    ):
        parser.error("pair slots must be two distinct positions in 0..3")
    count = args.grid * args.grid
    limit = 256 if args.pair_only else 65536
    if args.offset < 0 or args.offset + count > limit:
        parser.error("requested context range is outside the selected space")

    pitch = 2 * args.generation + 1400
    padding = args.generation + 650
    side = pitch * args.grid
    probe = probe_cells(args.probe_dx, args.probe_dy, args.transform, args.shape)
    cells: set[tuple[int, int]] = {
        (0, 0), (1, 0), (0, 1), (1, 1),
        (side - 2, side - 2), (side - 1, side - 2),
        (side - 2, side - 1), (side - 1, side - 1),
    }

    for tile in range(count):
        context = context_digits(args.offset + tile, args.pair_only, args.pair_slots)
        tile_x = (tile % args.grid) * pitch + padding
        tile_y = (tile // args.grid) * pitch + padding
        for digit_index, digit in enumerate(context):
            cells.update(
                glyph_cells(digit, tile_x - 19 + 10 * digit_index, tile_y - 400)
            )
        cells.update((tile_x + x, tile_y + y) for x, y in probe)

    args.output.write_text(wrapped_rle(cells, side), encoding="ascii")
    print(
        f"contexts={count} offset={args.offset} grid={args.grid} "
        f"generation={args.generation} pitch={pitch} padding={padding} "
        f"cells={len(cells)} side={side}"
    )


if __name__ == "__main__":
    main()
