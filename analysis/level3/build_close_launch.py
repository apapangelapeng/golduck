#!/usr/bin/env python3
"""Build the phase-optimized Level 3 variant from the current KOTH solver.

The solver's first seven Level 3 probes are c/2 orthogonal spaceships launched
at contestant row 90.  After 180 generations every one of those probes is in
the identical phase at row 0.  Starting there and observing at generation
2920 therefore produces the exact same viewing RLE as the original row-90,
generation-3100 configuration, while using the closest legal launch row.

The two later glider configurations are advanced along their period-four
trajectories as well.  The row-2 glider advances 8 generations and the row-50
gliders advance 200 generations.  These transformations preserve the exact
future state, cell count, and decoder behavior; they only remove simulation
work that happens before the ships leave the contestant rectangle.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "solution" / "koth-payload-1d26cbba-2e45-421a-8af9-9e0a75421eb5.wasm"
OUTPUT = ROOT / "solution" / "level3_close_fleet.wasm"
SOURCE_SHA256 = "d7d048fa6390356bba1c6f2e46d32b7cad6d1018b1fde451a67dbe2d32458918"

GENERATION_BLOCK = """    i32.const 3100
    i32.const 9940"""
PATCHED_GENERATION_BLOCK = """    i32.const 2920
    i32.const 9940"""
ROW_BLOCK = """    i32.const 90
    i32.const 289"""
PATCHED_ROW_BLOCK = """    i32.const 0
    i32.const 289"""

GLIDER_4400_BLOCK = """i32.const 4400
          i32.const 11196"""
PATCHED_GLIDER_4400_BLOCK = """i32.const 4392
          i32.const 11196"""
GLIDER_4500_BLOCK = """i32.const 4500
        i32.const 11568"""
PATCHED_GLIDER_4500_BLOCK = """i32.const 4300
        i32.const 11568"""

# Padding before the newline keeps each NUL-terminated static string exactly
# the same byte length, so all later data addresses remain unchanged.
GLIDER_4400_RLE = (
    "x = 2000, y = 100\\0a2$1662b3o$1662bo$1663bo!\\0a\\00"
)
PATCHED_GLIDER_4400_RLE = (
    "x = 2000, y = 100\\0a1660b3o$1660bo$1661bo!  \\0a\\00"
)
GLIDER_4500_A_RLE = (
    "x = 2000, y = 100\\0a50$124b3o$126bo$125bo!\\0a\\00"
)
PATCHED_GLIDER_4500_A_RLE = (
    "x = 2000, y = 100\\0a174b3o$176bo$175bo!   \\0a\\00"
)
GLIDER_4500_B_RLE = (
    "x = 2000, y = 100\\0a50$130b3o$132bo$131bo!\\0a\\00"
)
PATCHED_GLIDER_4500_B_RLE = (
    "x = 2000, y = 100\\0a180b3o$182bo$181bo!   \\0a\\00"
)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"required tool not found: {name}")
    return path


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one patch site for {old!r}")
    return text.replace(old, new)


def main() -> None:
    for original, patched in (
        (GLIDER_4400_RLE, PATCHED_GLIDER_4400_RLE),
        (GLIDER_4500_A_RLE, PATCHED_GLIDER_4500_A_RLE),
        (GLIDER_4500_B_RLE, PATCHED_GLIDER_4500_B_RLE),
    ):
        if len(original) != len(patched):
            raise SystemExit("a static RLE replacement changed byte length")

    actual_sha256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if actual_sha256 != SOURCE_SHA256:
        raise SystemExit(
            "source artifact changed: "
            f"expected {SOURCE_SHA256}, got {actual_sha256}"
        )

    wasm2wat = require_tool("wasm2wat")
    wat2wasm = require_tool("wat2wasm")
    validator = shutil.which("wasm-validate")

    with tempfile.TemporaryDirectory(prefix="golduck-level3-close-") as tmp:
        temporary_dir = Path(tmp)
        source_wat = temporary_dir / "source.wat"
        output_wat = temporary_dir / "close-launch.wat"
        output_wasm = temporary_dir / "close-launch.wasm"

        subprocess.run([wasm2wat, str(SOURCE), "-o", str(source_wat)], check=True)
        text = source_wat.read_text(encoding="utf-8")
        text = replace_once(text, GENERATION_BLOCK, PATCHED_GENERATION_BLOCK)
        text = replace_once(text, ROW_BLOCK, PATCHED_ROW_BLOCK)
        text = replace_once(text, GLIDER_4400_BLOCK, PATCHED_GLIDER_4400_BLOCK)
        text = replace_once(text, GLIDER_4500_BLOCK, PATCHED_GLIDER_4500_BLOCK)
        text = replace_once(text, GLIDER_4400_RLE, PATCHED_GLIDER_4400_RLE)
        text = replace_once(text, GLIDER_4500_A_RLE, PATCHED_GLIDER_4500_A_RLE)
        text = replace_once(text, GLIDER_4500_B_RLE, PATCHED_GLIDER_4500_B_RLE)
        output_wat.write_text(text, encoding="utf-8")
        subprocess.run([wat2wasm, str(output_wat), "-o", str(output_wasm)], check=True)
        if validator is not None:
            subprocess.run([validator, str(output_wasm)], check=True)
        output_wasm.replace(OUTPUT)

    print(OUTPUT)


if __name__ == "__main__":
    main()
