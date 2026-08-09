"""Characterize and generate a decoder table for the Spacefiller 2 probe."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import struct
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from golduck.rle import encode_rle, merge_placements, pattern_from_cells
from golduck.sim import _corner_blocks


ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "analysis/level2/spacefiller-2-single-copy.idea-lab.json"
TABLE_PATH = ROOT / "analysis/level2/spacefiller2-signatures-y30.json"
HEADER_PATH = ROOT / "solution/spacefiller2_table.h"
SHAPE_HEADER_PATH = ROOT / "solution/spacefiller2_shape.h"
BITMAP_PATH = ROOT / "analysis/level2/spacefiller2-bitmaps.json"
BITMAP_HEADER_PATH = ROOT / "solution/spacefiller2_bitmap_table.h"

GENERATION = 1500
CONTEXT_START = 28
CONTEXT_LENGTH = 8
CANVAS = (-1100, -1100, 2200, 2200)
VIEWING = (-500, 300, 1000, 200)
# Coordinates are relative to the returned 1000x200 viewing rectangle.
# Rows 0..29 can contain collision products from bits outside the selected
# eight-bit context.  Rows 30..80 are the guarded, locally reproducible return
# band used by the submitted decoder.
SIGNATURE_WINDOW = (250, 30, 500, 51)
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
UINT64_MASK = (1 << 64) - 1
BITMAP_WINDOW = (450, 30, 100, 10)


def legal_geometries(length: int = CONTEXT_LENGTH):
    """Yield 0=dead, 1=live/parity-0, 2=live/parity-1 strings.

    Consecutive live bits form one run and therefore share one parity.
    """

    for geometry in itertools.product(range(3), repeat=length):
        if all(
            not (left and right and left != right)
            for left, right in zip(geometry, geometry[1:])
        ):
            yield geometry


def geometry_mask(geometry: tuple[int, ...]) -> int:
    return sum((symbol != 0) << index for index, symbol in enumerate(geometry))


def encode_chunk(chunk: tuple[int, ...]) -> int:
    value = 0
    multiplier = 1
    for symbol in chunk:
        value += symbol * multiplier
        multiplier *= 3
    return value


def decode_chunk(value: int) -> tuple[int, int, int, int]:
    symbols = []
    for _ in range(4):
        symbols.append(value % 3)
        value //= 3
    return tuple(symbols)  # type: ignore[return-value]


def _fnv_bytes(value: int, data: bytes) -> int:
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & UINT64_MASK
    return value


def viewing_signature(
    rle: str,
    signature_window: tuple[int, int, int, int] | None = None,
    exclusion: tuple[int, int, int, int] | None = None,
) -> tuple[int, str]:
    """Hash maximal live intervals inside the normalized signal window."""

    window_x, window_y, window_width, window_height = (
        signature_window or SIGNATURE_WINDOW
    )
    frame_x = VIEWING[0] - CANVAS[0] + window_x
    frame_y = VIEWING[1] - CANVAS[1] + window_y
    raw = bytearray()
    x = 0
    y = 0
    count = 0
    has_count = False
    body = "".join(
        line
        for line in rle.splitlines()[1:]
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
            if frame_y <= y < frame_y + window_height:
                start = max(x, frame_x)
                end = min(x + repeat, frame_x + window_width)
                if start < end:
                    pieces = [(start, end)]
                    if exclusion is not None:
                        exclude_x, exclude_y, exclude_w, exclude_h = exclusion
                        relative_y = y - frame_y
                        if exclude_y <= relative_y < exclude_y + exclude_h:
                            absolute_exclude_start = frame_x + exclude_x
                            absolute_exclude_end = absolute_exclude_start + exclude_w
                            pieces = [
                                (piece_start, piece_end)
                                for piece_start, piece_end in (
                                    (start, min(end, absolute_exclude_start)),
                                    (max(start, absolute_exclude_end), end),
                                )
                                if piece_start < piece_end
                            ]
                    for piece_start, piece_end in pieces:
                        raw.extend(
                            struct.pack(
                                "<III",
                                y - frame_y,
                                piece_start - frame_x,
                                piece_end - frame_x,
                            )
                        )
            x += repeat
        elif token == "b":
            x += repeat
        elif token == "$":
            y += repeat
            x = 0
        elif token == "!":
            break
    return _fnv_bytes(FNV_OFFSET, raw), hashlib.sha256(raw).hexdigest()


def _load_probe_pattern(dx: int = 0, dy: int = 0):
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    placement = state["placements"][0]
    cells = {
        (placement["x"] + x + 500 + dx, placement["y"] + y + 100 + dy)
        for x, y in placement["cells"]
    }
    return pattern_from_cells(cells, 1000, 200)


def _secret_pattern(geometry: tuple[int, ...]):
    cells: set[tuple[int, int]] = set()
    for offset, symbol in enumerate(geometry):
        if symbol == 0:
            continue
        bit = CONTEXT_START + offset
        x = 3 * bit
        parity = symbol - 1
        cells.update(
            {
                (x, 0),
                (x, 1),
                (x + 3, 0),
                (x + 3, 1),
                (x + 1, parity),
                (x + 2, 1 - parity),
            }
        )
    return pattern_from_cells(cells, 193, 2)


def _combined_rle(geometry: tuple[int, ...], probe_pattern, corners) -> str:
    combined = merge_placements(
        [
            ((CANVAS[0]), CANVAS[1], corners),
            (-96, -401, _secret_pattern(geometry)),
            (-500, -100, probe_pattern),
        ],
        CANVAS,
    )
    return encode_rle(combined)


def viewing_bitmap(rle: str) -> int:
    """Return the 1000-bit central collision patch as a Python integer."""

    window_x, window_y, window_width, window_height = BITMAP_WINDOW
    frame_x = VIEWING[0] - CANVAS[0] + window_x
    frame_y = VIEWING[1] - CANVAS[1] + window_y
    bitmap = 0
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
            if frame_y <= y < frame_y + window_height:
                start = max(x, frame_x)
                end = min(x + repeat, frame_x + window_width)
                row_offset = (y - frame_y) * window_width
                for cell_x in range(start, end):
                    bitmap |= 1 << (row_offset + cell_x - frame_x)
            x += repeat
        elif token == "b":
            x += repeat
        elif token == "$":
            y += repeat
            x = 0
        elif token == "!":
            break
    return bitmap


def enumerate_bitmaps(
    bgolly: Path,
    bitmap_path: Path = BITMAP_PATH,
    probe_dx: int = 0,
    probe_dy: int = 0,
) -> dict[str, object]:
    probe_pattern = _load_probe_pattern(probe_dx, probe_dy)
    corners = _corner_blocks(CANVAS[2], CANVAS[3])
    geometries = list(legal_geometries())
    records = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="spacefiller2_bitmaps_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for index, geometry in enumerate(geometries, 1):
            input_path.write_text(
                _combined_rle(geometry, probe_pattern, corners),
                encoding="ascii",
            )
            completed = subprocess.run(
                [
                    str(bgolly), "-q", "-q", "-m", str(GENERATION),
                    "-o", str(output_path), str(input_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "bgolly failed")
            bitmap = viewing_bitmap(output_path.read_text(encoding="ascii"))
            records.append(
                {
                    "geometry": "".join(map(str, geometry)),
                    "bits": geometry_mask(geometry),
                    "left": encode_chunk(geometry[:4]),
                    "right": encode_chunk(geometry[4:]),
                    "bitmap": f"0x{bitmap:0250x}",
                }
            )
            if index % 200 == 0:
                print(
                    f"enumerated {index}/{len(geometries)} in "
                    f"{time.monotonic() - started:.1f}s",
                    flush=True,
                )
    payload = {
        "format": "golduck-spacefiller2-bitmaps",
        "version": 1,
        "generation": GENERATION,
        "context_start": CONTEXT_START,
        "context_length": CONTEXT_LENGTH,
        "probe_offset": {"x": probe_dx, "y": probe_dy},
        "bitmap_window": {
            "x": BITMAP_WINDOW[0], "y": BITMAP_WINDOW[1],
            "w": BITMAP_WINDOW[2], "h": BITMAP_WINDOW[3],
        },
        "records": records,
    }
    bitmap_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {bitmap_path} ({len(records)} records)")
    return payload


def analyze_bitmaps(payload: dict[str, object]) -> None:
    records = payload["records"]
    assert isinstance(records, list)
    prototypes = sorted({int(str(record["bitmap"]), 16) for record in records})
    distances = [
        (prototypes[left] ^ prototypes[right]).bit_count()
        for left in range(len(prototypes))
        for right in range(left + 1, len(prototypes))
    ]
    print(f"bitmap classes: {len(prototypes)}")
    print(f"minimum inter-class Hamming distance: {min(distances)}")
    print(f"median inter-class Hamming distance: {sorted(distances)[len(distances)//2]}")


def write_bitmap_header(payload: dict[str, object]) -> None:
    records = payload["records"]
    assert isinstance(records, list)
    prototypes = sorted({int(str(record["bitmap"]), 16) for record in records})
    class_by_bitmap = {bitmap: index for index, bitmap in enumerate(prototypes)}
    lines = [
        "#ifndef SPACEFILLER2_BITMAP_TABLE_H",
        "#define SPACEFILLER2_BITMAP_TABLE_H",
        "",
        "#include <stdint.h>",
        "",
        "#define SF_BITMAP_WORDS 16",
        f"#define SF_BITMAP_CLASS_COUNT {len(prototypes)}",
        f"#define SF_BITMAP_TRANSITION_COUNT {len(records)}",
        "",
        "typedef struct {",
        "    uint8_t left;",
        "    uint8_t right;",
        "    uint16_t class_id;",
        "} SfBitmapTransition;",
        "",
        f"static const uint64_t sf_bitmap_prototypes[{len(prototypes)}][SF_BITMAP_WORDS] = {{",
    ]
    for bitmap in prototypes:
        words = [(bitmap >> (64 * word)) & UINT64_MASK for word in range(16)]
        lines.append(
            "    {" + ", ".join(f"UINT64_C(0x{word:016x})" for word in words) + "},"
        )
    lines.extend(
        [
            "};",
            "",
            f"static const SfBitmapTransition sf_bitmap_transitions[{len(records)}] = {{",
        ]
    )
    for record in sorted(records, key=lambda item: (int(item["left"]), int(item["right"]))):
        class_id = class_by_bitmap[int(str(record["bitmap"]), 16)]
        lines.append(
            f"    {{{int(record['left'])}, {int(record['right'])}, {class_id}}},"
        )
    lines.extend(["};", "", "#endif", ""])
    BITMAP_HEADER_PATH.write_text("\n".join(lines), encoding="ascii")
    print(f"wrote {BITMAP_HEADER_PATH}")


def enumerate_table(
    bgolly: Path,
    table_path: Path = TABLE_PATH,
    probe_dx: int = 0,
    probe_dy: int = 0,
    signature_exclusion: tuple[int, int, int, int] | None = None,
    signature_window: tuple[int, int, int, int] = SIGNATURE_WINDOW,
) -> dict[str, object]:
    probe_pattern = _load_probe_pattern(probe_dx, probe_dy)
    corners = _corner_blocks(CANVAS[2], CANVAS[3])
    geometries = list(legal_geometries())
    records: list[dict[str, object]] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="spacefiller2_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for index, geometry in enumerate(geometries, 1):
            input_path.write_text(
                _combined_rle(geometry, probe_pattern, corners),
                encoding="ascii",
            )
            completed = subprocess.run(
                [
                    str(bgolly),
                    "-q",
                    "-q",
                    "-m",
                    str(GENERATION),
                    "-o",
                    str(output_path),
                    str(input_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "bgolly failed")
            signature, strong_signature = viewing_signature(
                output_path.read_text(encoding="ascii"),
                signature_window,
                exclusion=signature_exclusion,
            )
            records.append(
                {
                    "geometry": "".join(map(str, geometry)),
                    "bits": geometry_mask(geometry),
                    "left": encode_chunk(geometry[:4]),
                    "right": encode_chunk(geometry[4:]),
                    "signature": f"0x{signature:016x}",
                    "sha256": strong_signature,
                }
            )
            if index % 200 == 0:
                elapsed = time.monotonic() - started
                print(f"enumerated {index}/{len(geometries)} in {elapsed:.1f}s", flush=True)

    payload: dict[str, object] = {
        "format": "golduck-spacefiller2-signatures",
        "version": 1,
        "generation": GENERATION,
        "context_start": CONTEXT_START,
        "context_length": CONTEXT_LENGTH,
        "probe_offset": {"x": probe_dx, "y": probe_dy},
        "signature_window": {
            "x": signature_window[0],
            "y": signature_window[1],
            "w": signature_window[2],
            "h": signature_window[3],
        },
        "signature_exclusion": signature_exclusion,
        "records": records,
    }
    table_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {table_path} ({len(records)} records)")
    return payload


def enumerate_window_sweep(
    bgolly: Path,
    probe_dx: int = 0,
    probe_dy: int = 0,
    starts: tuple[int, ...] = (31, 32, 35, 40, 45, 50, 60),
) -> None:
    """Enumerate several lower guard boundaries in one simulation pass."""

    windows = {start: (250, start, 500, 81 - start) for start in starts}
    probe_pattern = _load_probe_pattern(probe_dx, probe_dy)
    corners = _corner_blocks(CANVAS[2], CANVAS[3])
    geometries = list(legal_geometries())
    records_by_start: dict[int, list[dict[str, object]]] = {
        start: [] for start in starts
    }
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="spacefiller2_sweep_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for index, geometry in enumerate(geometries, 1):
            input_path.write_text(
                _combined_rle(geometry, probe_pattern, corners),
                encoding="ascii",
            )
            completed = subprocess.run(
                [
                    str(bgolly),
                    "-q",
                    "-q",
                    "-m",
                    str(GENERATION),
                    "-o",
                    str(output_path),
                    str(input_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "bgolly failed")
            output_text = output_path.read_text(encoding="ascii")
            common = {
                "geometry": "".join(map(str, geometry)),
                "bits": geometry_mask(geometry),
                "left": encode_chunk(geometry[:4]),
                "right": encode_chunk(geometry[4:]),
            }
            for start, window in windows.items():
                signature, strong_signature = viewing_signature(
                    output_text, window
                )
                records_by_start[start].append(
                    {
                        **common,
                        "signature": f"0x{signature:016x}",
                        "sha256": strong_signature,
                    }
                )
            if index % 200 == 0:
                elapsed = time.monotonic() - started
                print(
                    f"enumerated {index}/{len(geometries)} in {elapsed:.1f}s",
                    flush=True,
                )

    phase_suffix = "" if probe_dy == 0 else f"-dy{probe_dy}"
    for start, records in records_by_start.items():
        path = ROOT / (
            f"analysis/level2/spacefiller2-signatures{phase_suffix}-y{start}.json"
        )
        window = windows[start]
        payload = {
            "format": "golduck-spacefiller2-signatures",
            "version": 1,
            "generation": GENERATION,
            "context_start": CONTEXT_START,
            "context_length": CONTEXT_LENGTH,
            "probe_offset": {"x": probe_dx, "y": probe_dy},
            "signature_window": {
                "x": window[0], "y": window[1],
                "w": window[2], "h": window[3],
            },
            "records": records,
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(records)} records)")


def load_table(table_path: Path = TABLE_PATH) -> dict[str, object]:
    return json.loads(table_path.read_text(encoding="utf-8"))


def analyze_pair(first: dict[str, object], second: dict[str, object]) -> bool:
    first_records = first["records"]
    second_records = second["records"]
    assert isinstance(first_records, list) and isinstance(second_records, list)
    second_by_geometry = {
        str(record["geometry"]): record
        for record in second_records
        if isinstance(record, dict)
    }
    classes: dict[tuple[str, str], set[int]] = defaultdict(set)
    for record in first_records:
        assert isinstance(record, dict)
        other = second_by_geometry[str(record["geometry"])]
        classes[(str(record["signature"]), str(other["signature"]))].add(
            int(record["bits"])
        )
    ambiguous = [values for values in classes.values() if len(values) > 1]
    print(f"paired output classes: {len(classes)}")
    print(f"paired ambiguous classes: {len(ambiguous)}")
    print(
        "paired eight-bit result: "
        + ("ambiguous" if ambiguous else "uniquely decodable")
    )
    if ambiguous:
        print(f"example ambiguity: {sorted(ambiguous[0])}")
    return not ambiguous


def analyze_table(payload: dict[str, object]) -> bool:
    records = payload["records"]
    assert isinstance(records, list)
    by_signature: dict[int, list[dict[str, object]]] = defaultdict(list)
    sha_by_fnv: dict[int, set[str]] = defaultdict(set)
    for record in records:
        assert isinstance(record, dict)
        signature = int(str(record["signature"]), 16)
        by_signature[signature].append(record)
        sha_by_fnv[signature].add(str(record["sha256"]))
    fnv_collisions = {
        signature: hashes
        for signature, hashes in sha_by_fnv.items()
        if len(hashes) > 1
    }

    print(f"legal parity geometries: {len(records)}")
    print(f"observable output classes: {len(by_signature)}")
    print(f"maximum raw information: {len(by_signature).bit_length() - 1:.0f}+ bits")
    print(f"FNV collisions across distinct outputs: {len(fnv_collisions)}")
    for bit in range(CONTEXT_LENGTH):
        decodable = all(
            len(
                {
                    (int(record["bits"]) >> bit) & 1
                    for record in output_records
                }
            )
            == 1
            for output_records in by_signature.values()
        )
        print(f"standalone bit {bit}: {'guaranteed' if decodable else 'ambiguous'}")

    outgoing: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        assert isinstance(record, dict)
        left = int(record["left"])
        right = int(record["right"])
        signature = int(str(record["signature"]), 16)
        outgoing[left][signature].append(right)

    chunks = sorted({int(record["left"]) for record in records} | {int(record["right"]) for record in records})
    initial = [code for code in chunks if decode_chunk(code)[:2] == (0, 0)]
    final = {code for code in chunks if decode_chunk(code)[2:] == (0, 0)}

    # Pair two globally consistent parity paths that emit the same 16 labels.
    # If a pair with different binary content reaches the right boundary, the
    # 16-run code is not guaranteed to identify the 64-bit value.
    states: dict[tuple[int, int, bool], None] = {}
    for left in initial:
        for right in initial:
            different = tuple(bool(v) for v in decode_chunk(left)) != tuple(
                bool(v) for v in decode_chunk(right)
            )
            states[(left, right, different)] = None

    for _ in range(16):
        next_states: dict[tuple[int, int, bool], None] = {}
        for left, other_left, different in states:
            common_labels = outgoing[left].keys() & outgoing[other_left].keys()
            for label in common_labels:
                for right in outgoing[left][label]:
                    for other_right in outgoing[other_left][label]:
                        binary_differs = tuple(bool(v) for v in decode_chunk(right)) != tuple(
                            bool(v) for v in decode_chunk(other_right)
                        )
                        next_states[(
                            right,
                            other_right,
                            different or binary_differs,
                        )] = None
        states = next_states

    ambiguous = any(
        different and left in final and right in final
        for left, right, different in states
    )
    print(
        "16 overlapping runs: "
        + ("ambiguous" if ambiguous else "all 64 binary bits uniquely decodable")
    )
    return not ambiguous and not fnv_collisions


def write_header(payload: dict[str, object]) -> None:
    records = payload["records"]
    assert isinstance(records, list)
    lines = [
        "#ifndef SPACEFILLER2_TABLE_H",
        "#define SPACEFILLER2_TABLE_H",
        "",
        "#include <stdint.h>",
        "",
        "typedef struct {",
        "    uint64_t signature;",
        "    uint8_t left;",
        "    uint8_t right;",
        "} SpacefillerTransition;",
        "",
        f"static const SpacefillerTransition spacefiller_transitions[{len(records)}] = {{",
    ]
    for record in sorted(
        records,
        key=lambda item: (int(str(item["signature"]), 16), int(item["left"]), int(item["right"])),
    ):
        lines.append(
            "    {UINT64_C(%s), %d, %d},"
            % (
                str(record["signature"]),
                int(record["left"]),
                int(record["right"]),
            )
        )
    lines.extend(
        [
            "};",
            "",
            f"#define SPACEFILLER_TRANSITION_COUNT {len(records)}",
            "",
            "#endif",
            "",
        ]
    )
    HEADER_PATH.write_text("\n".join(lines), encoding="ascii")
    print(f"wrote {HEADER_PATH}")


def write_combined_header(payloads: tuple[dict[str, object], ...]) -> None:
    record_sets = []
    for payload in payloads:
        records = payload["records"]
        assert isinstance(records, list)
        record_sets.append(records)
    counts = {len(records) for records in record_sets}
    if len(counts) != 1:
        raise ValueError("combined signature tables must have the same size")
    count = counts.pop()

    lines = [
        "#ifndef SPACEFILLER2_TABLE_H",
        "#define SPACEFILLER2_TABLE_H",
        "",
        "#include <stdint.h>",
        "",
        "typedef struct {",
        "    uint64_t signature;",
        "    uint8_t left;",
        "    uint8_t right;",
        "} SpacefillerTransition;",
        "",
        f"static const SpacefillerTransition spacefiller_transitions[{len(record_sets)}][{count}] = {{",
    ]
    for records in record_sets:
        lines.append("    {")
        for record in sorted(
            records,
            key=lambda item: (
                int(str(item["signature"]), 16),
                int(item["left"]),
                int(item["right"]),
            ),
        ):
            lines.append(
                "        {UINT64_C(%s), %d, %d},"
                % (
                    str(record["signature"]),
                    int(record["left"]),
                    int(record["right"]),
                )
            )
        lines.append("    },")
    lines.extend(
        [
            "};",
            "",
            f"#define SPACEFILLER_PHASE_COUNT {len(record_sets)}",
            f"#define SPACEFILLER_TRANSITION_COUNT {count}",
            "",
            "#endif",
            "",
        ]
    )
    HEADER_PATH.write_text("\n".join(lines), encoding="ascii")
    print(f"wrote {HEADER_PATH}")


def write_shape_header() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    placement = state["placements"][0]
    cells = sorted(
        (int(x), int(y)) for x, y in placement["cells"]
    )
    lines = [
        "#ifndef SPACEFILLER2_SHAPE_H",
        "#define SPACEFILLER2_SHAPE_H",
        "",
        "static const Cell spacefiller2[] = {",
    ]
    for index in range(0, len(cells), 6):
        group = cells[index : index + 6]
        lines.append(
            "    " + ", ".join(f"{{{x}, {y}}}" for x, y in group) + ","
        )
    lines.extend(
        [
            "};",
            "",
            f"#define SPACEFILLER2_CELL_COUNT {len(cells)}",
            "",
            "#endif",
            "",
        ]
    )
    SHAPE_HEADER_PATH.write_text("\n".join(lines), encoding="ascii")
    print(f"wrote {SHAPE_HEADER_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "enumerate",
            "analyze",
            "pair",
            "header",
            "combined-header",
            "shape-header",
            "sweep",
            "bitmaps",
            "analyze-bitmaps",
            "bitmap-header",
            "all",
        ),
        nargs="?",
        default="all",
    )
    parser.add_argument("--bgolly", type=Path, default=Path("/opt/homebrew/bin/bgolly"))
    parser.add_argument("--table", type=Path, default=TABLE_PATH)
    parser.add_argument("--other-table", type=Path)
    parser.add_argument("--probe-dx", type=int, default=0)
    parser.add_argument("--probe-dy", type=int, default=0)
    parser.add_argument("--window-y", type=int, default=SIGNATURE_WINDOW[1])
    args = parser.parse_args()

    if args.action in {"enumerate", "all"}:
        payload = enumerate_table(
            args.bgolly,
            args.table,
            args.probe_dx,
            args.probe_dy,
            signature_window=(
                SIGNATURE_WINDOW[0],
                args.window_y,
                SIGNATURE_WINDOW[2],
                SIGNATURE_WINDOW[1] + SIGNATURE_WINDOW[3] - args.window_y,
            ),
        )
    else:
        payload = load_table(args.table)
    if args.action in {"analyze", "all"}:
        analyze_table(payload)
    if args.action in {"header", "all"}:
        write_header(payload)
    if args.action == "pair":
        if args.other_table is None:
            parser.error("pair requires --other-table")
        analyze_pair(payload, load_table(args.other_table))
    if args.action == "combined-header":
        if args.other_table is None:
            parser.error("combined-header requires --other-table")
        write_combined_header((payload, load_table(args.other_table)))
    if args.action == "shape-header":
        write_shape_header()
    if args.action == "sweep":
        enumerate_window_sweep(
            args.bgolly,
            probe_dx=args.probe_dx,
            probe_dy=args.probe_dy,
        )
    if args.action == "bitmaps":
        enumerate_bitmaps(
            args.bgolly,
            BITMAP_PATH,
            probe_dx=args.probe_dx,
            probe_dy=args.probe_dy,
        )
    if args.action in {"analyze-bitmaps", "bitmap-header"}:
        bitmap_payload = json.loads(BITMAP_PATH.read_text(encoding="utf-8"))
        if args.action == "analyze-bitmaps":
            analyze_bitmaps(bitmap_payload)
        else:
            write_bitmap_header(bitmap_payload)


if __name__ == "__main__":
    main()
