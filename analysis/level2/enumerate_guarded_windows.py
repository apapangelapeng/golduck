#!/usr/bin/env python3
"""Enumerate Spacefiller-2 signatures for debris-free return regions.

The full-secret simulation can emit collision debris near the center of the
view that is absent when an eight-bit context is calibrated in isolation.
This tool evaluates several guarded unions of rectangles in one bgolly pass,
so candidate crops can be compared without rerunning all 1,393 legal parity
geometries for every rectangle.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from analysis.level2 import reverse_spacefiller as reverse


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "analysis/level2/guarded"
BGOLLY = Path("/opt/homebrew/bin/bgolly")

# Rectangles use coordinates in the returned 1000-by-200 view.  Every
# signature stores interval coordinates relative to the common (250, 30)
# origin, allowing the same simple hash routine to be used for unions.
ORIGIN = (250, 30)
REGIONS: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "y33": ((250, 33, 500, 48),),
    "y35": ((250, 35, 500, 46),),
    "y40": ((250, 40, 500, 41),),
    "y45": ((250, 45, 500, 36),),
    "y50": ((250, 50, 500, 31),),
    "outer35": ((250, 30, 240, 51), (525, 30, 225, 51)),
    "outer65": ((250, 30, 225, 51), (540, 30, 210, 51)),
    "hybrid33": (
        (250, 33, 500, 48),
        (250, 30, 240, 3),
        (525, 30, 225, 3),
    ),
    "hybrid35": (
        (250, 35, 500, 46),
        (250, 30, 240, 5),
        (525, 30, 225, 5),
    ),
}


def _view_intervals(rle: str) -> list[tuple[int, int, int]]:
    """Stream live intervals from the bounding return band in view coords."""

    frame_x = reverse.VIEWING[0] - reverse.CANVAS[0]
    frame_y = reverse.VIEWING[1] - reverse.CANVAS[1]
    left = frame_x + 250
    right = frame_x + 750
    top = frame_y + 30
    bottom = frame_y + 81
    intervals: list[tuple[int, int, int]] = []
    x = 0
    y = 0
    count = 0
    has_count = False
    body = "".join(
        line for line in rle.splitlines()[1:]
        if line and not line.startswith("#")
    )
    for token in body:
        if token.isdigit():
            count = count * 10 + int(token)
            has_count = True
            continue
        repeat = count if has_count else 1
        count = 0
        has_count = False
        if token == "o":
            if top <= y < bottom:
                start = max(x, left)
                end = min(x + repeat, right)
                if start < end:
                    intervals.append((y - frame_y, start - frame_x, end - frame_x))
            x += repeat
        elif token == "b":
            x += repeat
        elif token == "$":
            y += repeat
            x = 0
        elif token == "!":
            break
    return intervals


def _signature(
    intervals: list[tuple[int, int, int]],
    rectangles: tuple[tuple[int, int, int, int], ...],
):
    origin_x, origin_y = ORIGIN
    clipped: list[tuple[int, int, int]] = []
    for rect_x, rect_y, width, height in rectangles:
        left = rect_x
        right = left + width
        bottom = rect_y + height
        for y, interval_start, interval_end in intervals:
            if not (rect_y <= y < bottom):
                continue
            start = max(interval_start, left)
            end = min(interval_end, right)
            if start < end:
                clipped.append((y - origin_y, start - origin_x, end - origin_x))
    # Rectangles can be listed in any order.  Sorting also makes adjacent
    # pieces from a union deterministic.
    clipped.sort()
    raw = b"".join(struct.pack("<III", *interval) for interval in clipped)
    value = reverse.FNV_OFFSET
    for byte in raw:
        value = ((value ^ byte) * reverse.FNV_PRIME) & reverse.UINT64_MASK
    return value, hashlib.sha256(raw).hexdigest()


def _payload(name: str, records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": "golduck-spacefiller2-guarded-signatures",
        "version": 1,
        "generation": reverse.GENERATION,
        "context_start": reverse.CONTEXT_START,
        "context_length": reverse.CONTEXT_LENGTH,
        "probe_offset": {"x": 0, "y": 0},
        "signature_origin": {"x": ORIGIN[0], "y": ORIGIN[1]},
        "signature_regions": [
            {"x": x, "y": y, "w": width, "h": height}
            for x, y, width, height in REGIONS[name]
        ],
        "records": records,
    }


def main() -> None:
    geometries = list(reverse.legal_geometries())
    probe = reverse._load_probe_pattern()
    corners = reverse._corner_blocks(reverse.CANVAS[2], reverse.CANVAS[3])
    records = {name: [] for name in REGIONS}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="spacefiller2_guarded_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for index, geometry in enumerate(geometries, 1):
            input_path.write_text(
                reverse._combined_rle(geometry, probe, corners), encoding="ascii"
            )
            completed = subprocess.run(
                [
                    str(BGOLLY), "-q", "-q", "-m", str(reverse.GENERATION),
                    "-o", str(output_path), str(input_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "bgolly failed")
            intervals = _view_intervals(output_path.read_text(encoding="ascii"))
            for name, rectangles in REGIONS.items():
                signature, strong = _signature(intervals, rectangles)
                records[name].append(
                    {
                        "geometry": "".join(map(str, geometry)),
                        "bits": reverse.geometry_mask(geometry),
                        "left": reverse.encode_chunk(geometry[:4]),
                        "right": reverse.encode_chunk(geometry[4:]),
                        "signature": f"0x{signature:016x}",
                        "sha256": strong,
                    }
                )
            if index % 200 == 0:
                elapsed = time.monotonic() - started
                print(f"enumerated {index}/{len(geometries)} in {elapsed:.1f}s", flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, candidate_records in records.items():
        payload = _payload(name, candidate_records)
        path = OUTPUT_DIR / f"spacefiller2-{name}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        classes = len({record["signature"] for record in candidate_records})
        collisions: dict[str, set[str]] = defaultdict(set)
        for record in candidate_records:
            collisions[str(record["signature"])].add(str(record["sha256"]))
        fnv_collisions = sum(len(values) > 1 for values in collisions.values())
        print(
            f"{name}: {classes} classes, {fnv_collisions} FNV collisions -> {path}",
            flush=True,
        )


if __name__ == "__main__":
    main()
