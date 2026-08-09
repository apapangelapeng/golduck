#!/usr/bin/env python3
"""Calibrate Max151 generations in persistent, crop-aware Golly processes."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_max151_augmentations import WINDOWS, _information_metrics
from search_max151_schedule import Max151Model

CONTEXT_COUNT = 47_321
LUA_SCRIPT = ROOT / "analysis/level2/golly_max151_enumerate.lua"
DEFAULT_GOLLY = Path(
    "/Applications/golly-5.0-mac/Golly.app/Contents/MacOS/Golly"
)
DEFAULT_SHARDS = ROOT / "analysis/level2/max151-golly-shards"
DEFAULT_CHECKPOINT = ROOT / "analysis/level2/max151-augmentation-search.jsonl"
DEFAULT_REPORT = ROOT / "analysis/level2/max151-golly-exhaustive-report.json"


def _parse_int_list(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in value.split(",") if item)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from error
    if not result or any(number < 0 for number in result):
        raise argparse.ArgumentTypeError("values must be nonnegative integers")
    return result


def _parse_windows(value: str) -> tuple[str, ...]:
    result = tuple(item for item in value.split(",") if item)
    unknown = set(result) - set(WINDOWS)
    if not result or unknown:
        raise argparse.ArgumentTypeError(
            f"windows must come from {','.join(WINDOWS)}; unknown={sorted(unknown)}"
        )
    return result


def _ranges(parts: int) -> tuple[tuple[int, int, int], ...]:
    result = []
    for shard in range(parts):
        start = (CONTEXT_COUNT * shard) // parts
        end = (CONTEXT_COUNT * (shard + 1)) // parts
        result.append((shard, start, end))
    return tuple(result)


def _shard_complete(
    path: Path, start: int, end: int, windows: tuple[str, ...]
) -> bool:
    if not path.exists():
        return False
    ranks: list[int] = []
    has_footer = False
    header: tuple[str, ...] | None = None
    try:
        with path.open(encoding="ascii") as stream:
            for line in stream:
                if line.startswith("# rank"):
                    header = tuple(line.rstrip("\n").split("\t")[1:])
                elif line.startswith("# classes"):
                    has_footer = True
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
        and has_footer
        and len(ranks) == end - start
        and (not ranks or (ranks[0] == start and ranks[-1] == end - 1))
    )


def _run_shard(
    request: tuple[
        Path,
        Path,
        int,
        int,
        int,
        int,
        tuple[str, ...],
        Path,
    ]
) -> dict[str, object]:
    golly, lua_script, generation, shard, start, end, windows, output = request
    if _shard_complete(output, start, end, windows):
        return {
            "generation": generation,
            "shard": shard,
            "start": start,
            "end": end,
            "path": str(output),
            "seconds": 0.0,
            "cached": True,
            "attempts": 0,
        }

    for stale_temporary in output.parent.glob(f"{output.stem}.attempt-*.tmp"):
        stale_temporary.unlink(missing_ok=True)
    started = time.monotonic()
    failures: list[str] = []
    for attempt in range(1, 4):
        temporary = output.with_suffix(f".attempt-{attempt}.tmp")
        temporary.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "GOLDUCK_MAX151_GENERATION": str(generation),
                "GOLDUCK_MAX151_START_RANK": str(start),
                "GOLDUCK_MAX151_END_RANK": str(end),
                "GOLDUCK_MAX151_WINDOWS": ",".join(windows),
                "GOLDUCK_MAX151_OUTPUT": str(temporary),
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
        if completed.returncode == 0 and _shard_complete(
            temporary, start, end, windows
        ):
            temporary.replace(output)
            return {
                "generation": generation,
                "shard": shard,
                "start": start,
                "end": end,
                "path": str(output),
                "seconds": time.monotonic() - started,
                "cached": False,
                "attempts": attempt,
            }

        line_count = 0
        if temporary.exists():
            with temporary.open("rb") as stream:
                line_count = sum(1 for _ in stream)
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        failures.append(
            f"attempt {attempt}: rc={completed.returncode}, "
            f"lines={line_count}, detail={detail[-500:]}"
        )
        temporary.unlink(missing_ok=True)
        if attempt < 3:
            time.sleep(attempt)

    raise RuntimeError(
        f"Golly shard g{generation} {start}:{end} failed after 3 attempts; "
        + " | ".join(failures)
    )


def _load_generation(
    generation: int,
    ranges: tuple[tuple[int, int, int], ...],
    windows: tuple[str, ...],
    shard_dir: Path,
) -> dict[str, tuple[str, ...]]:
    labels: dict[str, list[str | None]] = {
        window: [None] * CONTEXT_COUNT for window in windows
    }
    for _, start, end in ranges:
        path = shard_dir / f"g{generation}-{start}-{end}.tsv"
        with path.open(encoding="ascii") as stream:
            header: tuple[str, ...] | None = None
            for line in stream:
                if line.startswith("# rank"):
                    header = tuple(line.rstrip("\n").split("\t")[1:])
                    if not all(window in header for window in windows):
                        raise ValueError(f"unexpected windows in {path}: {header}")
                elif line.startswith("#"):
                    continue
                else:
                    if header is None:
                        raise ValueError(f"missing header in {path}")
                    fields = line.rstrip("\n").split("\t")
                    rank = int(fields[0])
                    if not start <= rank < end or len(fields) != len(header) + 1:
                        raise ValueError(f"invalid row in {path}: {line.rstrip()}")
                    for window in windows:
                        signature = fields[header.index(window) + 1]
                        if labels[window][rank] is not None:
                            raise ValueError(f"duplicate rank {rank} for {window}")
                        labels[window][rank] = signature
    result: dict[str, tuple[str, ...]] = {}
    for window, values in labels.items():
        missing = [index for index, value in enumerate(values) if value is None]
        if missing:
            raise ValueError(
                f"generation {generation}/{window} is missing {len(missing)} ranks"
            )
        result[window] = tuple(value for value in values if value is not None)
    return result


def _validate_current_partition(
    labels: tuple[str, ...], production_classes: tuple[int, ...]
) -> None:
    signature_to_class: dict[str, int] = {}
    class_to_signature: dict[int, str] = {}
    for rank, (signature, production_class) in enumerate(
        zip(labels, production_classes)
    ):
        previous_class = signature_to_class.setdefault(signature, production_class)
        previous_signature = class_to_signature.setdefault(production_class, signature)
        if previous_class != production_class or previous_signature != signature:
            raise AssertionError(
                f"generation-1326 partition differs from production at rank {rank}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golly", type=Path, default=DEFAULT_GOLLY)
    parser.add_argument("--lua-script", type=Path, default=LUA_SCRIPT)
    parser.add_argument(
        "--generations", type=_parse_int_list, default=(1326, 1425, 1775)
    )
    parser.add_argument(
        "--windows", type=_parse_windows, default=("legacy", "wide")
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--shards", type=int, default=12)
    parser.add_argument("--shard-dir", type=Path, default=DEFAULT_SHARDS)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if args.workers < 1 or args.shards < 1:
        parser.error("workers and shards must be positive")
    if not args.golly.exists():
        parser.error(f"Golly executable not found: {args.golly}")
    if not args.lua_script.exists():
        parser.error(f"Lua enumerator not found: {args.lua_script}")

    args.shard_dir.mkdir(parents=True, exist_ok=True)
    ranges = _ranges(args.shards)
    requests = []
    for generation in args.generations:
        for shard, start, end in ranges:
            output = args.shard_dir / f"g{generation}-{start}-{end}.tsv"
            requests.append(
                (
                    args.golly,
                    args.lua_script,
                    generation,
                    shard,
                    start,
                    end,
                    args.windows,
                    output,
                )
            )

    print(
        f"running {len(requests)} persistent Golly shards with {args.workers} workers",
        flush=True,
    )
    shard_results: list[dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as executor:
        futures = [executor.submit(_run_shard, request) for request in requests]
        for position, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            shard_results.append(result)
            print(
                f"{position}/{len(requests)} complete: "
                f"g{result['generation']} {result['start']}:{result['end']} "
                f"seconds={result['seconds']:.1f} cached={result['cached']} "
                f"attempts={result['attempts']}",
                flush=True,
            )

    model = Max151Model()
    full_context_indices = tuple(range(len(model.contexts)))
    context_digest = hashlib.sha256(
        repr(full_context_indices).encode("ascii")
    ).hexdigest()
    rows: list[dict[str, object]] = []
    for generation in args.generations:
        labels = _load_generation(generation, ranges, args.windows, args.shard_dir)
        if generation == 1326 and "legacy" in labels:
            _validate_current_partition(labels["legacy"], tuple(model.classes))
            print("generation 1326 legacy partition exactly matches production", flush=True)
        metrics = {
            window: _information_metrics(model.contexts, list(window_labels))
            for window, window_labels in labels.items()
        }
        row: dict[str, object] = {
            "stage": "exhaustive",
            "candidate": {
                "candidate_id": f"baseline-g{generation}",
                "kind": "baseline",
                "generation": generation,
                "additions": [],
                "description": f"unmodified Max151 at generation {generation}",
            },
            "context_seed_indices_sha256": context_digest,
            "metrics": metrics,
            "signatures": labels,
            "backend": "golly-lua-persistent",
            "worker_seconds": sum(
                cast(float, result["seconds"])
                for result in shard_results
                if cast(int, result["generation"]) == generation
            ),
        }
        rows.append(row)
        print(
            f"g{generation}: "
            + ", ".join(
                f"{window} classes={values['classes']} "
                f"forced={values['mean_forced_bits']:.6f}"
                for window, values in metrics.items()
            ),
            flush=True,
        )

    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with args.checkpoint.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    payload = {
        "format": "golduck-max151-golly-exhaustive",
        "version": 1,
        "generations": args.generations,
        "windows": args.windows,
        "workers": args.workers,
        "shards": args.shards,
        "shard_results": shard_results,
        "rows": [
            {
                "candidate": row["candidate"],
                "metrics": row["metrics"],
                "backend": row["backend"],
                "worker_seconds": row["worker_seconds"],
            }
            for row in rows
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"appended {len(rows)} exhaustive rows to {args.checkpoint}", flush=True)
    print(f"wrote {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
