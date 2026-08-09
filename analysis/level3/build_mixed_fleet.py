#!/usr/bin/env python3
"""Build the phase-optimized heterogeneous Level 3 fleet.

The source solver multiplexes four to six c/2 orthogonal ships in its early
runs and uses two different three-glider mixtures as fallbacks.  Every patch
below advances a complete periodic fleet to the latest equivalent phase that
still fits in the contestant rectangle.  Decoder inputs therefore remain
exactly identical while the maximum simulated generation is reduced.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    ROOT
    / "solution"
    / "koth-payload-ca57433f-f67f-4393-ae81-214056481f56.wasm"
)
OUTPUT = ROOT / "solution" / "level3_mixed_fleet.wasm"
SOURCE_SHA256 = "bf4eb28ef051cad6d91539ec994a590e02c70fb40495fc262b64905862db593b"

EDGE_GLIDER_RLE = (
    "x = 2000, y = 100\\0a2$1662b3o$1662bo$1663bo!\\0a\\00"
)
PATCHED_EDGE_GLIDER_RLE = (
    "x = 2000, y = 100\\0a1660b3o$1660bo$1661bo!  \\0a\\00"
)

MIX_A_RLE = (
    "x = 2000, y = 100, rule = B3/S23\\0a"
    "1819b2o$1819bobo$1819bo2$342bo$342b2o$341bobo59$"
    "109b3o$111bo$110bo!\\0a\\00"
)
PATCHED_MIX_A_RLE = (
    "x = 2000, y = 100, rule = B3/S23\\0a"
    "1818b3o$1818bo$1819bo2$342b2o$343b2o$342bo58$"
    "110b2o$109bobo$111bo! \\0a\\00"
)

MIX_B_RLE = (
    "x = 2000, y = 100, rule = B3/S23\\0a"
    "1822b2o$1822bobo$1822bo2$334bo$334b2o$333bobo64$"
    "110b2o$109bobo$111bo!\\0a\\00"
)
PATCHED_MIX_B_RLE = (
    "x = 2000, y = 100, rule = B3/S23\\0a"
    "1821b3o$1821bo$1822bo2$334b2o$335b2o$334bo64$"
    "110b3o$112bo$111bo!     \\0a\\00"
)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"required tool not found: {name}")
    return path


def replace_exactly(text: str, old: str, new: str, count: int) -> str:
    actual = text.count(old)
    if actual != count:
        raise SystemExit(
            f"expected {count} patch sites for {old!r}, found {actual}"
        )
    return text.replace(old, new)


def main() -> None:
    for original, patched in (
        (EDGE_GLIDER_RLE, PATCHED_EDGE_GLIDER_RLE),
        (MIX_A_RLE, PATCHED_MIX_A_RLE),
        (MIX_B_RLE, PATCHED_MIX_B_RLE),
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

    with tempfile.TemporaryDirectory(prefix="golduck-level3-mixed-") as tmp:
        temporary_dir = Path(tmp)
        source_wat = temporary_dir / "source.wat"
        output_wat = temporary_dir / "mixed-fleet.wat"
        output_wasm = temporary_dir / "mixed-fleet.wasm"

        subprocess.run([wasm2wat, str(SOURCE), "-o", str(source_wat)], check=True)
        text = source_wat.read_text(encoding="utf-8")

        # Five sites keep the guest's generation checks and actual host calls
        # synchronized; three sites move both the base and multiplexed lanes.
        text = replace_exactly(text, "i32.const 3100", "i32.const 2920", 5)
        text = replace_exactly(text, "i32.const 90\n", "i32.const 0\n", 3)
        text = replace_exactly(text, "i32.const 4400", "i32.const 4392", 1)
        text = replace_exactly(text, "i32.const 4560", "i32.const 4558", 1)
        text = replace_exactly(text, "i32.const 4582", "i32.const 4580", 1)

        text = replace_exactly(text, EDGE_GLIDER_RLE, PATCHED_EDGE_GLIDER_RLE, 1)
        text = replace_exactly(text, MIX_A_RLE, PATCHED_MIX_A_RLE, 1)
        text = replace_exactly(text, MIX_B_RLE, PATCHED_MIX_B_RLE, 1)

        output_wat.write_text(text, encoding="utf-8")
        subprocess.run([wat2wasm, str(output_wat), "-o", str(output_wasm)], check=True)
        if validator is not None:
            subprocess.run([validator, str(output_wasm)], check=True)
        output_wasm.replace(OUTPUT)

    print(OUTPUT)


if __name__ == "__main__":
    main()
