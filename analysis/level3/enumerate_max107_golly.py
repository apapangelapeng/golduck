#!/usr/bin/env python3
"""Run and combine exhaustive Level 3 Max107 Golly calibration shards."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTEXT_COUNT = 1 << 16
LUA_SCRIPT = ROOT / "analysis/level3/golly_max107_enumerate.lua"
DEFAULT_GOLLY = Path(
    "/Applications/golly-5.0-mac/Golly.app/Contents/MacOS/Golly"
)
DEFAULT_SHARD_DIR = ROOT / "analysis/level3/max107-golly-shards"
DEFAULT_TABLE = ROOT / "analysis/level3/max107-context-table.json"


def _ranges(parts: int) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (
            shard,
            (CONTEXT_COUNT * shard) // parts,
            (CONTEXT_COUNT * (shard + 1)) // parts,
        )
        for shard in range(parts)
    )


def _shard_complete(
    path: Path, start: int, end: int, windows: tuple[str, ...]
) -> bool:
    if not path.exists():
        return False
    ranks: list[int] = []
    header: tuple[str, ...] | None = None
    complete = False
    try:
        with path.open(encoding="ascii") as stream:
            for line in stream:
                if line.startswith("# rank"):
                    header = tuple(line.rstrip("\n").split("\t")[1:])
                elif line.startswith("# complete"):
                    complete = True
                elif line and not line.startswith("#"):
                    fields = line.rstrip("\n").split("\t")
                    if header is None or len(fields) != len(header) + 1:
                        return False
                    ranks.append(int(fields[0]))
    except (OSError, UnicodeError, ValueError):
        return False
    return (
        header is not None
        and all(window in header for window in windows)
        and complete
        and len(ranks) == end - start
        and (not ranks or (ranks[0] == start and ranks[-1] == end - 1))
    )


def _run_shard(
    request: tuple[
        Path,
        Path,
        int,
        str,
        int,
        int,
        int,
        tuple[str, ...],
        Path,
    ]
) -> dict[str, object]:
    (
        golly,
        lua_script,
        generation,
        algorithm,
        shard,
        start,
        end,
        windows,
        output,
    ) = request
    if _shard_complete(output, start, end, windows):
        return {
            "shard": shard,
            "start": start,
            "end": end,
            "seconds": 0.0,
            "cached": True,
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    started = time.monotonic()
    environment = os.environ.copy()
    environment.update(
        {
            "GOLDUCK_L3_MAX107_GENERATION": str(generation),
            "GOLDUCK_L3_MAX107_START_RANK": str(start),
            "GOLDUCK_L3_MAX107_END_RANK": str(end),
            "GOLDUCK_L3_MAX107_OUTPUT": str(temporary),
            "GOLDUCK_L3_MAX107_ALGORITHM": algorithm,
            "GOLDUCK_L3_MAX107_WINDOWS": ",".join(windows),
        }
    )
    completed = subprocess.run(
        [str(golly), str(lua_script)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode or not _shard_complete(
        temporary, start, end, windows
    ):
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RuntimeError(
            f"Golly shard {start}:{end} failed with status "
            f"{completed.returncode}: {detail[-500:]}"
        )
    temporary.replace(output)
    return {
        "shard": shard,
        "start": start,
        "end": end,
        "seconds": time.monotonic() - started,
        "cached": False,
    }


def _load(
    shard_dir: Path,
    generation: int,
    ranges: tuple[tuple[int, int, int], ...],
    windows: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str | None]] = {
        window: [None] * CONTEXT_COUNT for window in windows
    }
    for shard, start, end in ranges:
        path = shard_dir / f"g{generation}-s{shard}-{start}-{end}.tsv"
        with path.open(encoding="ascii") as stream:
            header: tuple[str, ...] | None = None
            for line in stream:
                if line.startswith("# rank"):
                    header = tuple(line.rstrip("\n").split("\t")[1:])
                elif line.startswith("#"):
                    continue
                else:
                    if header is None:
                        raise ValueError(f"missing header in {path}")
                    fields = line.rstrip("\n").split("\t")
                    rank = int(fields[0])
                    if not start <= rank < end:
                        raise ValueError(f"rank {rank} is outside {start}:{end}")
                    for window in windows:
                        values[window][rank] = fields[header.index(window) + 1]
    result: dict[str, tuple[str, ...]] = {}
    for window, window_values in values.items():
        if any(value is None for value in window_values):
            raise ValueError(f"incomplete {window} calibration")
        result[window] = tuple(str(value) for value in window_values)
    return result


def _build_table(
    generation: int,
    labels: dict[str, tuple[str, ...]],
    output: Path,
) -> dict[str, object]:
    windows: dict[str, object] = {}
    for name, signatures in labels.items():
        unique = sorted(set(signatures))
        class_by_signature = {
            signature: class_id for class_id, signature in enumerate(unique)
        }
        classes = tuple(class_by_signature[signature] for signature in signatures)
        groups: dict[int, list[int]] = defaultdict(list)
        for context, class_id in enumerate(classes):
            groups[class_id].append(context)

        forced_total = 0
        exact_contexts = 0
        entropy = 0.0
        for contexts in groups.values():
            common = 0xFFFF
            first = contexts[0]
            for context in contexts[1:]:
                common &= ~(first ^ context)
            forced_total += len(contexts) * (common & 0xFFFF).bit_count()
            exact_contexts += len(contexts) == 1
            probability = len(contexts) / CONTEXT_COUNT
            entropy -= probability * math.log2(probability)

        windows[name] = {
            "signatures": unique,
            "classes": classes,
            "metrics": {
                "class_count": len(unique),
                "largest_class": max(map(len, groups.values())),
                "entropy_bits": entropy,
                "mean_forced_context_bits": forced_total / CONTEXT_COUNT,
                "unique_contexts": exact_contexts,
            },
        }

    payload: dict[str, object] = {
        "format": "golduck-level3-max107-context-table",
        "version": 1,
        "generation": generation,
        "context_digits": 4,
        "windows": windows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    for name, raw in windows.items():
        assert isinstance(raw, dict)
        print(f"{name}: {raw['metrics']}")
    return payload


def _parse_windows(value: str) -> tuple[str, ...]:
    values = tuple(item for item in value.split(",") if item)
    allowed = {"narrow", "tall", "wide", "full"}
    if not values or set(values) - allowed:
        raise argparse.ArgumentTypeError(
            f"windows must come from {','.join(sorted(allowed))}"
        )
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golly", type=Path, default=DEFAULT_GOLLY)
    parser.add_argument("--lua-script", type=Path, default=LUA_SCRIPT)
    parser.add_argument("--generation", type=int, default=2050)
    parser.add_argument("--algorithm", choices=("QuickLife", "HashLife"), default="QuickLife")
    parser.add_argument("--windows", type=_parse_windows, default=("narrow",))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--shards", type=int, default=16)
    parser.add_argument("--shard-dir", type=Path, default=DEFAULT_SHARD_DIR)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    args = parser.parse_args()

    if args.workers < 1 or args.shards < 1:
        parser.error("workers and shards must be positive")
    ranges = _ranges(args.shards)
    requests = []
    for shard, start, end in ranges:
        output = args.shard_dir / (
            f"g{args.generation}-s{shard}-{start}-{end}.tsv"
        )
        requests.append(
            (
                args.golly,
                args.lua_script,
                args.generation,
                args.algorithm,
                shard,
                start,
                end,
                args.windows,
                output,
            )
        )

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(args.workers) as executor:
        for position, result in enumerate(executor.map(_run_shard, requests), 1):
            print(
                f"shard {result['shard']} ({result['start']}:{result['end']}) "
                f"completed in {float(result['seconds']):.1f}s "
                f"[{position}/{len(requests)}]",
                flush=True,
            )
    print(f"calibration elapsed: {time.monotonic() - started:.1f}s")
    labels = _load(args.shard_dir, args.generation, ranges, args.windows)
    _build_table(args.generation, labels, args.table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
