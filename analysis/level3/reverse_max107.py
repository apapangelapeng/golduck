#!/usr/bin/env python3
"""Calibrate the backward-propagating Max107 probe on Level 3 glyphs.

The 107-cell predecessor is the probe used by ``max107_adaptive11`` on
Level 2.  On Level 3, a launch at contestant row zero reaches the glyphs and
returns a local collision scar to the viewing rectangle near generation
2050.  This tool enumerates the 256 ordered pairs of hexadecimal glyphs and
records the returned 120-by-48 bitmap class.

Four causally disjoint experiments are packed into each bgolly invocation.
The packed worlds are only an offline calibration optimization; generated
contestant patterns still contain one 107-cell predecessor per run.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from golduck.level3 import _FONT_ROWS
from golduck.rle import encode_rle, parse_rle, pattern_from_cells


DEFAULT_BGOLLY = Path("/opt/homebrew/bin/bgolly")
if not DEFAULT_BGOLLY.exists():
    DEFAULT_BGOLLY = ROOT / "bgolly"

DEFAULT_TABLE = ROOT / "analysis/level3/max107-pairs-g2050-dx0.json"

GENERATION = 2050
PROBE_DX = 0

# Absolute Level 3 coordinates.  The normalized Max107 predecessor begins
# three columns to the left of the first glyph, matching its Level 2 launch.
SECRET_ORIGIN = (-79, -400)
PROBE_ORIGIN = (-82, 350)
VIEW_ORIGIN = (-500, -100)
VIEW_SIZE = (1000, 200)

# Relative to the returned view.  This is the Level 2 decoder's 120-by-48
# crop translated with the predecessor.  It contains the complete local
# return class at generation 2050.
SIGNATURE_WINDOW = (370, 0, 120, 48)

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
UINT64_MASK = (1 << 64) - 1

# Normalized from the 107-cell pattern embedded in max107_adaptive11.
MAX107_RLE = """x = 25, y = 23, rule = B3/S23
19b3o$13bo5bo2bo$13bo5bo$11bo2bo4bo$11b4o4bo$3bo7b2o3bo2bo$o9bo9bo$
b2o5bo3bo10bo$4bo3bo3bo9bo$2obob2obo3bo3b2o4bo$4bo5bo3b2o4bo3bo$
2bob2o2bo3bo3bo2b2obo$o3bo4b2o3bo5bo$2bo4b2o3bo3bob2obob2o$
2bo9bo3bo3bo$bo10bo3bo5b2o$4bo9bo9bo$5bo2bo3b2o7bo$5bo4b4o$
5bo4bo2bo$5bo5bo$2bo2bo5bo$3b3o!
"""


def _cells(pattern) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y, intervals in pattern.rows.items()
        for left, right in intervals
        for x in range(left, right)
    }


MAX107_CELLS = _cells(parse_rle(MAX107_RLE, require_header=True))


def glyph_cells(text: str) -> set[tuple[int, int]]:
    """Return one 16-digit Level 3 secret in absolute coordinates."""

    if len(text) != 16 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("expected sixteen lowercase hexadecimal glyphs")
    result: set[tuple[int, int]] = set()
    for digit, character in enumerate(text):
        for y, row in enumerate(_FONT_ROWS[character]):
            result.update(
                (SECRET_ORIGIN[0] + 10 * digit + x, SECRET_ORIGIN[1] + y)
                for x, value in enumerate(row)
                if value == "#"
            )
    return result


def probe_cells(dx: int = PROBE_DX) -> set[tuple[int, int]]:
    return {
        (PROBE_ORIGIN[0] + dx + x, PROBE_ORIGIN[1] + y)
        for x, y in MAX107_CELLS
    }


def _packed_input(
    pair_values: tuple[int, ...],
    generation: int,
    probe_dx: int,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Pack up to four pair experiments into one causally disjoint world."""

    if not 1 <= len(pair_values) <= 4:
        raise ValueError("a packed batch must contain one through four pairs")

    # A six-thousand-cell pitch leaves more than one full light cone between
    # experiments at every supported generation near the 2050 return phase.
    pitch = max(6000, 2 * generation + 1200)
    padding = pitch // 2
    width = height = 2 * pitch
    origins = (
        (padding, padding),
        (padding + pitch, padding),
        (padding, padding + pitch),
        (padding + pitch, padding + pitch),
    )[: len(pair_values)]
    cells = {
        (0, 0), (1, 0), (0, 1), (1, 1),
        (width - 2, height - 2), (width - 1, height - 2),
        (width - 2, height - 1), (width - 1, height - 1),
    }
    probe = probe_cells(probe_dx)
    for pair, (origin_x, origin_y) in zip(pair_values, origins):
        text = f"{pair:02x}" + "0" * 14
        experiment = probe | glyph_cells(text)
        cells.update(
            (origin_x + x, origin_y + y) for x, y in experiment
        )
    return encode_rle(pattern_from_cells(cells, width, height)), origins


def _fnv_byte(value: int, byte: int) -> int:
    return ((value ^ byte) * FNV_PRIME) & UINT64_MASK


def return_signature(pattern, origin_x: int, origin_y: int, probe_dx: int) -> int:
    """Hash the exact 120-by-48 return bitmap in row-major byte order."""

    window_x, window_y, width, height = SIGNATURE_WINDOW
    left = origin_x + VIEW_ORIGIN[0] + window_x + probe_dx
    top = origin_y + VIEW_ORIGIN[1] + window_y
    value = FNV_OFFSET
    for y in range(top, top + height):
        row = pattern.rows.get(y)
        for byte_x in range(0, width, 8):
            packed = 0
            if row is not None:
                for bit in range(8):
                    x = left + byte_x + bit
                    if any(interval_left <= x < interval_right for interval_left, interval_right in row):
                        packed |= 1 << bit
            value = _fnv_byte(value, packed)
    return value


def _enumerate_batch(
    request: tuple[tuple[int, ...], int, int, str]
) -> list[tuple[int, int]]:
    pair_values, generation, probe_dx, bgolly_text = request
    bgolly = Path(bgolly_text)
    input_rle, origins = _packed_input(pair_values, generation, probe_dx)
    with tempfile.TemporaryDirectory(prefix="golduck-l3-max107-") as directory:
        directory_path = Path(directory)
        input_path = directory_path / "input.rle"
        output_path = directory_path / "output.rle"
        input_path.write_text(input_rle, encoding="ascii")
        completed = subprocess.run(
            [
                str(bgolly), "-q", "-q", "-m", str(generation),
                "-o", str(output_path), str(input_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                f"bgolly failed with status {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
        output = parse_rle(
            output_path.read_text(encoding="ascii"), require_header=True
        )
    return [
        (pair, return_signature(output, x, y, probe_dx))
        for pair, (x, y) in zip(pair_values, origins)
    ]


def enumerate_pairs(
    bgolly: Path,
    table_path: Path,
    generation: int,
    probe_dx: int,
    workers: int,
) -> dict[str, object]:
    batches = [
        tuple(range(start, min(start + 4, 256)))
        for start in range(0, 256, 4)
    ]
    requests = [
        (batch, generation, probe_dx, str(bgolly)) for batch in batches
    ]
    records: dict[int, int] = {}
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(workers) as executor:
        for position, batch_records in enumerate(executor.map(_enumerate_batch, requests), 1):
            records.update(batch_records)
            if position % 8 == 0 or position == len(requests):
                print(
                    f"enumerated {4 * position}/256 pairs in "
                    f"{time.monotonic() - started:.1f}s",
                    flush=True,
                )

    payload: dict[str, object] = {
        "format": "golduck-level3-max107-pairs",
        "version": 1,
        "generation": generation,
        "probe_dx": probe_dx,
        "signature_window": {
            "x": SIGNATURE_WINDOW[0] + probe_dx,
            "y": SIGNATURE_WINDOW[1],
            "w": SIGNATURE_WINDOW[2],
            "h": SIGNATURE_WINDOW[3],
        },
        "records": [
            {"pair": f"{pair:02x}", "signature": f"0x{records[pair]:016x}"}
            for pair in range(256)
        ],
    }
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {table_path}")
    return payload


def analyze(payload: dict[str, object]) -> None:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("table has no records list")
    by_signature: dict[int, list[int]] = defaultdict(list)
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise ValueError("invalid record")
        by_signature[int(str(raw["signature"]), 16)].append(
            int(str(raw["pair"]), 16)
        )

    forced_total = 0
    exact_pairs = 0
    for pairs in by_signature.values():
        common = 0xFF
        first = pairs[0]
        for pair in pairs[1:]:
            common &= ~(first ^ pair)
        forced_total += len(pairs) * common.bit_count()
        exact_pairs += len(pairs) == 1

    print(f"output classes: {len(by_signature)}")
    print(f"largest class: {max(map(len, by_signature.values()))}")
    print(f"uniquely decoded pairs: {exact_pairs}/256")
    print(f"mean forced pair bits: {forced_total / 256:.4f}/8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("enumerate", "analyze", "all"), nargs="?", default="all")
    parser.add_argument("--bgolly", type=Path, default=DEFAULT_BGOLLY)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--generation", type=int, default=GENERATION)
    parser.add_argument("--probe-dx", type=int, default=PROBE_DX)
    parser.add_argument("--workers", type=int, default=min(2, max(1, os.cpu_count() or 1)))
    args = parser.parse_args()

    if args.action in {"enumerate", "all"}:
        payload = enumerate_pairs(
            args.bgolly, args.table, args.generation, args.probe_dx, args.workers
        )
    else:
        payload = json.loads(args.table.read_text(encoding="utf-8"))
    if args.action in {"analyze", "all"}:
        analyze(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
