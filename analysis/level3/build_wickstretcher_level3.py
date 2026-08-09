#!/usr/bin/env python3
"""Build the adaptive Level 3 Wickstretcher-1 edge decoder."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from golduck.rle import encode_rle, parse_rle, pattern_from_cells

PATTERN = ROOT / "analysis/level3/wickstretcher1.rle"
SIGNATURES = ROOT / "analysis/level3/wick-g3300-context3-signatures.json"
SOURCE = ROOT / "solution/wickstretcher_level3.c"
OUTPUT = ROOT / "solution/wickstretcher_level3.wasm"
GENERATION = 3300
PROBES = (
    "north:1075:g3300",
    "mirror:1062:g3300",
    "north:1068:g3300",
    "north:1064:g3300",
)


def pattern_cells(orientation: str) -> set[tuple[int, int]]:
    pattern = parse_rle(PATTERN.read_text(encoding="ascii"))
    source = {
        (x, y)
        for y, intervals in pattern.rows.items()
        for left, right in intervals
        for x in range(left, right)
    }
    if orientation == "north":
        return {(y, 48 - x) for x, y in source}
    if orientation == "mirror":
        return {(15 - y, 48 - x) for x, y in source}
    raise ValueError(f"unknown orientation {orientation!r}")


def contestant_rle(x_origin: int, orientation: str) -> str:
    placed = {(x_origin + x, y) for x, y in pattern_cells(orientation)}
    return encode_rle(pattern_from_cells(placed, 2000, 100))


def c_string(text: str) -> str:
    lines = text.splitlines(keepends=True)
    return "\n".join(
        '    "'
        + line.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        + '"'
        for line in lines
    )


def source_text() -> str:
    document = json.loads(SIGNATURES.read_text(encoding="utf-8"))
    signatures = document["signatures"]
    if int(document["generation"]) != GENERATION:
        raise ValueError("signature generation does not match the solver")
    context_count = len(signatures[PROBES[0]])
    for probe in PROBES:
        if probe not in signatures or len(signatures[probe]) != context_count:
            raise ValueError(f"inconsistent signature table for {probe}")

    rles = []
    for probe in PROBES:
        orientation, x_text, _ = probe.split(":")
        rles.append(contestant_rle(int(x_text), orientation))

    rows = []
    for context in range(context_count):
        hashes = ", ".join(
            f"0x{int(signatures[probe][context]):016x}ULL" for probe in PROBES
        )
        rows.append(f"    {{{{{hashes}}}, 0x{context & 15:x}}},")

    patterns = "\n\n".join(
        f"static const char PROBE_{index}[] =\n{c_string(rle)};"
        for index, rle in enumerate(rles)
    )
    table = "\n".join(rows)
    pointers = ", ".join(f"PROBE_{index}" for index in range(len(PROBES)))

    return f'''typedef unsigned long long u64;

__attribute__((import_module("env"), import_name("run")))
int host_run(int level, const char *rle, int length, int generations, char *out);

__attribute__((import_module("env"), import_name("submit")))
int host_submit(int level, u64 value, u64 known_mask, u64 guess_mask);

__attribute__((import_module("env"), import_name("finalize")))
int host_finalize(void);

#define SCRATCH_CAPACITY (1024 * 1024)
#define PROBE_COUNT {len(PROBES)}
#define CONTEXT_COUNT {context_count}

char scratch_ptr[SCRATCH_CAPACITY];
char scratch_cap;

{patterns}

static const char *const PROBES[PROBE_COUNT] = {{{pointers}}};

typedef struct {{
  u64 hashes[PROBE_COUNT];
  unsigned char digit;
}} DecodeRow;

static const DecodeRow ROWS[CONTEXT_COUNT] = {{
{table}
}};

static int string_length(const char *text) {{
  int length = 0;
  while (text[length]) ++length;
  return length;
}}

static u64 fnv1a64(const char *data, int length) {{
  u64 value = 14695981039346656037ULL;
  for (int index = 0; index < length; ++index) {{
    value ^= (u64)(unsigned char)data[index];
    value *= 1099511628211ULL;
  }}
  return value;
}}

static unsigned forced_bits(const unsigned char *active, int *value) {{
  int first = -1;
  unsigned mask = 0xFU;
  for (int row = 0; row < CONTEXT_COUNT; ++row) {{
    if (!active[row]) continue;
    int candidate = (int)ROWS[row].digit;
    if (first < 0) first = candidate;
    else mask &= (unsigned)~(first ^ candidate) & 0xFU;
  }}
  if (first < 0) return 0;
  *value = first;
  return mask;
}}

__attribute__((visibility("default")))
void run_entry(void) {{
  unsigned char active[CONTEXT_COUNT];
  for (int row = 0; row < CONTEXT_COUNT; ++row) active[row] = 1;

  for (int probe = 0; probe < PROBE_COUNT; ++probe) {{
    int length = host_run(
        3, PROBES[probe], string_length(PROBES[probe]),
        {GENERATION}, scratch_ptr);
    if (length <= 0) break;
    u64 observed = fnv1a64(scratch_ptr, length);
    for (int row = 0; row < CONTEXT_COUNT; ++row) {{
      if (active[row] && ROWS[row].hashes[probe] != observed) active[row] = 0;
    }}
    int value = 0;
    unsigned mask = forced_bits(active, &value);
    if (mask == 0xFU) {{
      host_submit(3, (u64)value, 0xFULL, 0);
      host_finalize();
      return;
    }}
  }}
  int value = 0;
  unsigned mask = forced_bits(active, &value);
  if (mask) host_submit(3, (u64)value, (u64)mask, 0);
  host_finalize();
}}
'''


def main() -> None:
    SOURCE.write_text(source_text(), encoding="ascii")
    clang = Path("/opt/homebrew/opt/llvm/bin/clang")
    if not clang.exists():
        discovered = shutil.which("clang")
        if discovered is None:
            raise SystemExit("clang was not found")
        clang = Path(discovered)
    subprocess.run(
        [
            str(clang),
            "--target=wasm32",
            "-O3",
            "-nostdlib",
            "-fno-builtin",
            "-Wl,--no-entry",
            "-Wl,--export-memory",
            "-Wl,--export=scratch_ptr",
            "-Wl,--export=scratch_cap",
            "-Wl,--allow-undefined",
            "-Wl,--strip-all",
            "-Wl,--export-dynamic",
            str(SOURCE),
            "-o",
            str(OUTPUT),
        ],
        check=True,
    )
    validator = shutil.which("wasm-validate")
    if validator is not None:
        subprocess.run([validator, str(OUTPUT)], check=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
