#!/usr/bin/env python3
"""Fuzz additions to the Max151 Level 2 probe.

The search has two deliberately different candidate families:

* one extra live cell in or near the 27-by-23 Max151 predecessor; and
* a translated/transformed glider, LWSS, MWSS, or HWSS near that predecessor.

Each candidate is evaluated against legal 12-trit Level 2 contexts with exact
B3/S23 evolution in bgolly.  Small stages are useful for an interactive scan;
larger stages can be left running and resumed from the JSONL checkpoint.

The most useful statistic is ``mean_forced_bits``.  It is the mean number of
binary secret positions fixed by an output class on the tested context panel.
It is measured on the same contexts for every candidate and for an unmodified
Max151 baseline at each generation.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_max151_schedule import Max151Model

from golduck.rle import encode_rle, merge_placements, parse_rle, pattern_from_cells
from golduck.sim import _corner_blocks

SOURCE = ROOT / "solution/max151_adaptive8.c"
DEFAULT_BGOLLY = Path("/opt/homebrew/bin/bgolly")
if not DEFAULT_BGOLLY.exists():
    DEFAULT_BGOLLY = ROOT / "bgolly"

CANVAS = (-1800, -1800, 3600, 3600)
VIEW = (-500, 300, 1000, 200)
CONTESTANT_ORIGIN = (-500, -100)
SECRET_ORIGIN = (-96, -401)
SECRET_WIDTH = 193
SECRET_START = 29
CONTEXT_LENGTH = 12
CONTEXT_FIRST_BIT = SECRET_START - 3
BASE_ORIGIN = (398 + 3 * SECRET_START, 0)
BASE_GENERATION = 1326
UINT64_MASK = (1 << 64) - 1

# All three signatures come from the same simulation.  ``legacy`` is the
# production decoder crop.  ``wide`` catches nearby extra returns, while
# ``full`` is an intentionally permissive discovery view that must later pass
# an outside-context locality audit.
WINDOWS = {
    "legacy": (440, 0, 120, 48),
    "wide": (320, 0, 360, 120),
    "full": (0, 0, 1000, 200),
}

SPACESHIPS: dict[str, tuple[tuple[int, int], ...]] = {
    "glider": ((1, 0), (2, 1), (0, 2), (1, 2), (2, 2)),
    "lwss": (
        (1, 0), (4, 0), (0, 1), (0, 2), (4, 2),
        (0, 3), (1, 3), (2, 3), (3, 3),
    ),
    "mwss": (
        (3, 0), (1, 1), (5, 1), (0, 2), (0, 3), (5, 3),
        (0, 4), (1, 4), (2, 4), (3, 4), (4, 4),
    ),
    "hwss": (
        (3, 0), (4, 0), (1, 1), (6, 1), (0, 2), (0, 3), (6, 3),
        (0, 4), (1, 4), (2, 4), (3, 4), (4, 4), (5, 4),
    ),
}


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: str
    generation: int
    additions: tuple[tuple[int, int], ...]
    description: str


def _base_cells_normalized() -> frozenset[tuple[int, int]]:
    source = SOURCE.read_text(encoding="ascii")
    match = re.search(
        r"static const char\* SF2PAT\[SF2PROBES\] = \{\s*"
        r"(\"(?:[^\"\\]|\\.)*\")",
        source,
        re.DOTALL,
    )
    if match is None:
        raise ValueError(f"could not extract SF2PAT[0] from {SOURCE}")
    pattern = parse_rle(ast.literal_eval(match.group(1)), require_header=True)
    cells = {
        (x, y)
        for y, intervals in pattern.rows.items()
        for left, right in intervals
        for x in range(left, right)
    }
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return frozenset((x - min_x, y - min_y) for x, y in cells)


BASE_NORMALIZED = _base_cells_normalized()
BASE_CELLS = frozenset(
    (BASE_ORIGIN[0] + x, BASE_ORIGIN[1] + y) for x, y in BASE_NORMALIZED
)
BASE_BOUNDS = (
    min(x for x, _ in BASE_CELLS),
    min(y for _, y in BASE_CELLS),
    max(x for x, _ in BASE_CELLS),
    max(y for _, y in BASE_CELLS),
)
CORNER_PATTERN = _corner_blocks(CANVAS[2], CANVAS[3])


def _normalize(cells: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    unique = set(cells)
    min_x = min(x for x, _ in unique)
    min_y = min(y for _, y in unique)
    return tuple(sorted((x - min_x, y - min_y) for x, y in unique))


def _transform(
    cells: Iterable[tuple[int, int]], rotation: int, mirrored: bool
) -> tuple[tuple[int, int], ...]:
    transformed: list[tuple[int, int]] = []
    for source_x, source_y in cells:
        x = -source_x if mirrored else source_x
        y = source_y
        for _ in range(rotation % 4):
            x, y = -y, x
        transformed.append((x, y))
    return _normalize(transformed)


SPACESHIP_PHASES: dict[str, tuple[tuple[tuple[int, int], ...], ...]] = {}
for _name, _shape in SPACESHIPS.items():
    _phases = {
        _transform(_shape, rotation, mirrored)
        for rotation in range(4)
        for mirrored in (False, True)
    }
    SPACESHIP_PHASES[_name] = tuple(sorted(_phases))


def _secret_pattern(geometry: tuple[int, ...]):
    """Build a legal Level 2 strip, either local (12 trits) or full (64)."""

    if len(geometry) == CONTEXT_LENGTH:
        positioned = enumerate(geometry, CONTEXT_FIRST_BIT)
    elif len(geometry) == 64:
        positioned = enumerate(geometry)
    else:
        raise ValueError("geometry must contain 12 or 64 trits")

    cells: set[tuple[int, int]] = set()
    for bit, symbol in positioned:
        if not symbol:
            continue
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
    return pattern_from_cells(cells, SECRET_WIDTH, 2)


def _contestant_pattern(candidate: Candidate):
    cells = set(BASE_CELLS)
    cells.update(candidate.additions)
    return pattern_from_cells(cells, 1000, 200)


def _combined_rle(candidate: Candidate, geometry: tuple[int, ...]) -> str:
    combined = merge_placements(
        [
            (CANVAS[0], CANVAS[1], CORNER_PATTERN),
            (SECRET_ORIGIN[0], SECRET_ORIGIN[1], _secret_pattern(geometry)),
            (
                CONTESTANT_ORIGIN[0],
                CONTESTANT_ORIGIN[1],
                _contestant_pattern(candidate),
            ),
        ],
        CANVAS,
    )
    return encode_rle(combined)


def _view_cells(rle: str) -> set[tuple[int, int]]:
    frame_left = VIEW[0] - CANVAS[0]
    frame_top = VIEW[1] - CANVAS[1]
    frame_right = frame_left + VIEW[2]
    frame_bottom = frame_top + VIEW[3]
    cells: set[tuple[int, int]] = set()
    x = 0
    y = 0
    count = 0
    has_count = False
    body = "".join(
        line
        for line in rle.splitlines()
        if line and not line.startswith("#") and not line.lstrip().startswith("x")
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
            if frame_top <= y < frame_bottom:
                left = max(x, frame_left)
                right = min(x + repeat, frame_right)
                cells.update(
                    (cell_x - frame_left, y - frame_top)
                    for cell_x in range(left, right)
                )
            x += repeat
        elif token == "b":
            x += repeat
        elif token == "$":
            y += repeat
            x = 0
            if y >= frame_bottom:
                break
        elif token == "!":
            break
    return cells


def _window_signature(
    cells: set[tuple[int, int]], window: tuple[int, int, int, int]
) -> str:
    left, top, width, height = window
    selected = sorted(
        (y - top, x - left)
        for x, y in cells
        if left <= x < left + width and top <= y < top + height
    )
    digest = hashlib.blake2b(repr(selected).encode("ascii"), digest_size=16)
    return f"{len(selected)}:{digest.hexdigest()}"


def _pattern_window_signature(
    pattern,
    left: int,
    top: int,
    width: int,
    height: int,
) -> str:
    selected: list[tuple[int, int]] = []
    for y in range(top, top + height):
        intervals = pattern.rows.get(y)
        if intervals is None:
            continue
        for interval_left, interval_right in intervals:
            clipped_left = max(left, interval_left)
            clipped_right = min(left + width, interval_right)
            selected.extend(
                (y - top, x - left) for x in range(clipped_left, clipped_right)
            )
    digest = hashlib.blake2b(repr(selected).encode("ascii"), digest_size=16)
    return f"{len(selected)}:{digest.hexdigest()}"


def _absolute_cells(pattern, origin: tuple[int, int]) -> set[tuple[int, int]]:
    origin_x, origin_y = origin
    return {
        (origin_x + x, origin_y + y)
        for y, intervals in pattern.rows.items()
        for left, right in intervals
        for x in range(left, right)
    }


def _batched_rle(
    candidate: Candidate,
    indexed_geometries: tuple[tuple[int, tuple[int, ...]], ...],
) -> tuple[str, tuple[tuple[int, int, int], ...]]:
    """Place causally disjoint experiments side by side in one Life world."""

    generation = candidate.generation
    padding = generation + 1000
    pitch = 2 * generation + 2400
    columns = math.ceil(math.sqrt(len(indexed_geometries)))
    rows = math.ceil(len(indexed_geometries) / columns)
    width = pitch * columns
    height = pitch * rows
    cells = {
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
        (width - 2, height - 2),
        (width - 1, height - 2),
        (width - 2, height - 1),
        (width - 1, height - 1),
    }
    contestant_cells = _absolute_cells(_contestant_pattern(candidate), CONTESTANT_ORIGIN)
    origins: list[tuple[int, int, int]] = []
    for tile, (index, geometry) in enumerate(indexed_geometries):
        origin_x = (tile % columns) * pitch + padding
        origin_y = (tile // columns) * pitch + padding
        origins.append((index, origin_x, origin_y))
        cells.update(
            (origin_x + x, origin_y + y) for x, y in contestant_cells
        )
        secret_cells = _absolute_cells(_secret_pattern(geometry), SECRET_ORIGIN)
        cells.update((origin_x + x, origin_y + y) for x, y in secret_cells)
    return encode_rle(pattern_from_cells(cells, width, height)), tuple(origins)


def _run_bgolly(
    bgolly: Path,
    generation: int,
    input_path: Path,
    output_path: Path,
) -> None:
    completed = subprocess.run(
        [
            str(bgolly),
            "-q",
            "-q",
            "-m",
            str(generation),
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


def _evaluate_shard(
    request: tuple[Candidate, tuple[tuple[int, tuple[int, ...]], ...], str, int]
) -> tuple[str, list[tuple[int, dict[str, str]]], float]:
    candidate, indexed_geometries, bgolly_text, batch_size = request
    bgolly = Path(bgolly_text)
    records: list[tuple[int, dict[str, str]]] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="max151_augment_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        if batch_size <= 1:
            contestant = _contestant_pattern(candidate)
            for index, geometry in indexed_geometries:
                combined = merge_placements(
                    [
                        (CANVAS[0], CANVAS[1], CORNER_PATTERN),
                        (SECRET_ORIGIN[0], SECRET_ORIGIN[1], _secret_pattern(geometry)),
                        (CONTESTANT_ORIGIN[0], CONTESTANT_ORIGIN[1], contestant),
                    ],
                    CANVAS,
                )
                input_path.write_text(encode_rle(combined), encoding="ascii")
                _run_bgolly(
                    bgolly, candidate.generation, input_path, output_path
                )
                cells = _view_cells(output_path.read_text(encoding="ascii"))
                records.append(
                    (
                        index,
                        {
                            name: _window_signature(cells, window)
                            for name, window in WINDOWS.items()
                        },
                    )
                )
        else:
            for offset in range(0, len(indexed_geometries), batch_size):
                batch = indexed_geometries[offset : offset + batch_size]
                batch_rle, origins = _batched_rle(candidate, batch)
                input_path.write_text(batch_rle, encoding="ascii")
                _run_bgolly(
                    bgolly, candidate.generation, input_path, output_path
                )
                output = parse_rle(
                    output_path.read_text(encoding="ascii"), require_header=True
                )
                for index, origin_x, origin_y in origins:
                    signatures: dict[str, str] = {}
                    for name, (window_x, window_y, width, height) in WINDOWS.items():
                        left = origin_x + VIEW[0] + window_x
                        top = origin_y + VIEW[1] + window_y
                        signatures[name] = _pattern_window_signature(
                            output, left, top, width, height
                        )
                    records.append((index, signatures))
    return candidate.candidate_id, records, time.monotonic() - started


def _information_metrics(
    geometries: tuple[tuple[int, ...], ...], signatures: list[str]
) -> dict[str, float | int]:
    if len(geometries) != len(signatures) or not geometries:
        raise ValueError("geometries and signatures must be nonempty and aligned")
    groups: dict[str, list[int]] = defaultdict(list)
    for geometry, signature in zip(geometries, signatures):
        binary = sum(bool(symbol) << bit for bit, symbol in enumerate(geometry))
        groups[signature].append(binary)

    total = len(geometries)
    weighted_forced = 0
    weighted_binary_ambiguity = 0.0
    binary_exact_records = 0
    entropy = 0.0
    for masks in groups.values():
        first = masks[0]
        forced = (1 << CONTEXT_LENGTH) - 1
        for mask in masks[1:]:
            forced &= ~(first ^ mask)
        forced &= (1 << CONTEXT_LENGTH) - 1
        weighted_forced += len(masks) * forced.bit_count()
        distinct_binary = len(set(masks))
        weighted_binary_ambiguity += len(masks) * math.log2(distinct_binary)
        if distinct_binary == 1:
            binary_exact_records += len(masks)
        probability = len(masks) / total
        entropy -= probability * math.log2(probability)

    return {
        "contexts": total,
        "classes": len(groups),
        "entropy_bits": entropy,
        "mean_forced_bits": weighted_forced / total,
        "mean_log2_binary_ambiguity": weighted_binary_ambiguity / total,
        "binary_exact_fraction": binary_exact_records / total,
        "largest_class": max(map(len, groups.values())),
    }


def _candidate_metrics(
    geometries: tuple[tuple[int, ...], ...],
    records: list[tuple[int, dict[str, str]]],
) -> dict[str, dict[str, float | int]]:
    ordered = [windows for _, windows in sorted(records)]
    return {
        window: _information_metrics(
            geometries, [record[window] for record in ordered]
        )
        for window in WINDOWS
    }


def _candidate_score(metrics: dict[str, dict[str, float | int]]) -> tuple[float, ...]:
    # The guarded production crop wins ties.  Wide/full discovery signatures
    # can rank a candidate, but must later be checked against outside context.
    best = max(
        metrics.items(),
        key=lambda item: (
            float(item[1]["mean_forced_bits"]),
            float(item[1]["entropy_bits"]),
            item[0] == "legacy",
        ),
    )
    return (
        float(best[1]["mean_forced_bits"]),
        float(best[1]["entropy_bits"]),
        float(best[1]["binary_exact_fraction"]),
        -float(best[1]["mean_log2_binary_ambiguity"]),
        float(metrics["legacy"]["mean_forced_bits"]),
    )


def _baseline_candidate(generation: int) -> Candidate:
    return Candidate(
        candidate_id=f"baseline-g{generation}",
        kind="baseline",
        generation=generation,
        additions=(),
        description=f"unmodified Max151 at generation {generation}",
    )


def _single_cell_candidates(
    generator: random.Random,
    samples: int,
    generations: tuple[int, ...],
    margin_x: int,
    margin_y: int,
    effective_only: bool,
) -> list[Candidate]:
    min_x, min_y, max_x, max_y = BASE_BOUNDS
    positions = [
        (x, y)
        for y in range(max(0, min_y - margin_y), min(199, max_y + margin_y) + 1)
        for x in range(max(0, min_x - margin_x), min(999, max_x + margin_x) + 1)
        if (x, y) not in BASE_CELLS
    ]
    if effective_only:
        influenced_positions = {
            (base_x + dx, base_y + dy)
            for base_x, base_y in BASE_CELLS
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
        }
        positions = [position for position in positions if position in influenced_positions]
    generator.shuffle(positions)
    if samples > 0:
        positions = positions[:samples]
    candidates: list[Candidate] = []
    for x, y in positions:
        for generation in generations:
            candidates.append(
                Candidate(
                    candidate_id=f"cell-x{x}-y{y}-g{generation}",
                    kind="cell",
                    generation=generation,
                    additions=((x, y),),
                    description=f"one live cell at ({x},{y})",
                )
            )
    return candidates


def _spaceship_candidates(
    generator: random.Random,
    samples: int,
    generations: tuple[int, ...],
    margin_x: int,
    margin_y: int,
) -> list[Candidate]:
    min_x, _, max_x, max_y = BASE_BOUNDS
    candidates: list[Candidate] = []
    seen: set[tuple[object, ...]] = set()
    attempts = 0
    while len(candidates) < samples and attempts < max(100, samples * 20):
        attempts += 1
        name = generator.choice(tuple(SPACESHIP_PHASES))
        phases = SPACESHIP_PHASES[name]
        phase_index = generator.randrange(len(phases))
        shape = phases[phase_index]
        width = max(x for x, _ in shape) + 1
        height = max(y for _, y in shape) + 1
        left = generator.randint(max(0, min_x - margin_x), min(999 - width + 1, max_x + margin_x))
        top = generator.randint(0, min(199 - height + 1, max_y + margin_y))
        generation = generator.choice(generations)
        key = (name, phase_index, left, top, generation)
        if key in seen:
            continue
        seen.add(key)
        placed = tuple(sorted((left + x, top + y) for x, y in shape))
        additions = tuple(cell for cell in placed if cell not in BASE_CELLS)
        if not additions:
            continue
        candidates.append(
            Candidate(
                candidate_id=(
                    f"ship-{name}-p{phase_index}-x{left}-y{top}-g{generation}"
                ),
                kind="spaceship",
                generation=generation,
                additions=additions,
                description=(
                    f"{name} phase {phase_index} at ({left},{top}); "
                    f"{len(additions)} added cells"
                ),
            )
        )
    return candidates


def _sample_context_indices(
    model: Max151Model, count: int, seed: int
) -> tuple[int, ...]:
    if count <= 0 or count >= len(model.contexts):
        return tuple(range(len(model.contexts)))

    generator = random.Random(seed)
    by_class: dict[int, list[int]] = defaultdict(list)
    for index, observation_class in enumerate(model.classes):
        by_class[observation_class].append(index)

    chosen: set[int] = set()
    special = [
        tuple([0] * CONTEXT_LENGTH),
        tuple([1] * CONTEXT_LENGTH),
        tuple([2] * CONTEXT_LENGTH),
    ]
    for context in special:
        chosen.add(model.rank[context])

    # Half of the panel is made of pairs that the current Max151 observation
    # aliases despite different binary values.  These are sensitive tests for
    # genuinely new information rather than merely a surviving return.
    ambiguous: list[tuple[int, list[int]]] = []
    for observation_class, indices in by_class.items():
        masks = {
            sum(bool(symbol) << bit for bit, symbol in enumerate(model.contexts[index]))
            for index in indices
        }
        if len(masks) > 1:
            ambiguous.append((observation_class, indices))
    generator.shuffle(ambiguous)
    pair_target = count // 2
    for _, indices in ambiguous:
        generator.shuffle(indices)
        first = indices[0]
        first_mask = sum(
            bool(symbol) << bit
            for bit, symbol in enumerate(model.contexts[first])
        )
        second = next(
            (
                index
                for index in indices[1:]
                if sum(
                    bool(symbol) << bit
                    for bit, symbol in enumerate(model.contexts[index])
                )
                != first_mask
            ),
            None,
        )
        if second is None:
            continue
        chosen.update((first, second))
        if len(chosen) >= pair_target:
            break

    remaining = [index for index in range(len(model.contexts)) if index not in chosen]
    generator.shuffle(remaining)
    chosen.update(remaining[: max(0, count - len(chosen))])
    return tuple(sorted(chosen))[:count]


def _run_stage(
    stage: str,
    candidates: list[Candidate],
    context_indices: tuple[int, ...],
    model: Max151Model,
    bgolly: Path,
    workers: int,
    batch_size: int,
    checkpoint: Path | None,
    cached: dict[tuple[str, str], dict[str, object]],
) -> dict[str, dict[str, object]]:
    geometries = tuple(model.contexts[index] for index in context_indices)
    context_digest = hashlib.sha256(
        repr(context_indices).encode("ascii")
    ).hexdigest()
    results: dict[str, dict[str, object]] = {}
    pending: list[Candidate] = []
    for candidate in candidates:
        cached_row = cached.get((stage, candidate.candidate_id))
        if (
            cached_row is not None
            and cached_row.get("context_seed_indices_sha256") == context_digest
        ):
            results[candidate.candidate_id] = cached_row
        else:
            pending.append(candidate)

    if pending:
        print(
            f"{stage}: evaluating {len(pending)} candidates x "
            f"{len(context_indices)} contexts with {workers} workers",
            flush=True,
        )

    requests: list[
        tuple[Candidate, tuple[tuple[int, tuple[int, ...]], ...], str, int]
    ] = []
    # For one or two exhaustive candidates, spread context shards across all
    # workers.  Ordinary screen/refine stages use one request per candidate.
    shard_count = (
        workers * 4
        if len(pending) < max(2, workers // 2) and len(context_indices) >= 1024
        else 1
    )
    for candidate in pending:
        for shard in range(shard_count):
            indexed = tuple(
                (position, model.contexts[index])
                for position, index in enumerate(context_indices)
                if position % shard_count == shard
            )
            requests.append((candidate, indexed, str(bgolly), batch_size))

    accumulated: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    elapsed_by_candidate: dict[str, float] = defaultdict(float)
    completed_shards: dict[str, int] = defaultdict(int)
    finished_requests = 0
    candidate_by_id = {candidate.candidate_id: candidate for candidate in pending}
    if requests:
        with concurrent.futures.ProcessPoolExecutor(workers) as executor:
            for candidate_id, records, elapsed in executor.map(
                _evaluate_shard, requests, chunksize=1
            ):
                accumulated[candidate_id].extend(records)
                elapsed_by_candidate[candidate_id] += elapsed
                completed_shards[candidate_id] += 1
                finished_requests += 1
                if shard_count > 1 and finished_requests % workers == 0:
                    print(
                        f"{stage}: {finished_requests}/{len(requests)} shards complete",
                        flush=True,
                    )
                if completed_shards[candidate_id] != shard_count:
                    continue
                candidate = candidate_by_id[candidate_id]
                metrics = _candidate_metrics(geometries, accumulated[candidate_id])
                row: dict[str, object] = {
                    "stage": stage,
                    "candidate": asdict(candidate),
                    "context_seed_indices_sha256": context_digest,
                    "metrics": metrics,
                    "worker_seconds": elapsed_by_candidate[candidate_id],
                }
                if stage == "exhaustive":
                    ordered_records = [
                        windows for _, windows in sorted(accumulated[candidate_id])
                    ]
                    row["signatures"] = {
                        window: [record[window] for record in ordered_records]
                        for window in WINDOWS
                    }
                results[candidate_id] = row
                if checkpoint is not None:
                    checkpoint.parent.mkdir(parents=True, exist_ok=True)
                    with checkpoint.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, sort_keys=True) + "\n")
                if len(results) % 25 == 0 or len(results) == len(candidates):
                    best = max(
                        results.values(),
                        key=lambda item: _candidate_score(item["metrics"]),  # type: ignore[arg-type]
                    )
                    best_candidate = best["candidate"]
                    best_metrics = best["metrics"]
                    assert isinstance(best_candidate, dict)
                    assert isinstance(best_metrics, dict)
                    best_window, window_metrics = max(
                        best_metrics.items(),
                        key=lambda item: (
                            float(item[1]["mean_forced_bits"]),
                            float(item[1]["entropy_bits"]),
                        ),
                    )
                    print(
                        f"{stage}: {len(results)}/{len(candidates)} complete; "
                        f"best={best_candidate['candidate_id']} "
                        f"{best_window} forced={window_metrics['mean_forced_bits']:.3f} "
                        f"entropy={window_metrics['entropy_bits']:.3f}",
                        flush=True,
                    )
    return results


def _load_checkpoint(path: Path | None) -> dict[tuple[str, str], dict[str, object]]:
    rows: dict[tuple[str, str], dict[str, object]] = {}
    if path is None or not path.exists():
        return rows
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                candidate = row["candidate"]
                rows[(str(row["stage"]), str(candidate["candidate_id"]))] = row
            except (KeyError, TypeError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"invalid checkpoint row {line_number} in {path}: {error}"
                ) from error
    return rows


def _parse_int_list(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in value.split(",") if item)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from error
    if not result or any(number < 0 for number in result):
        raise argparse.ArgumentTypeError("generation list must contain nonnegative integers")
    return result


def _top_rows(
    results: dict[str, dict[str, object]], count: int
) -> list[dict[str, object]]:
    return sorted(
        results.values(),
        key=lambda row: _candidate_score(row["metrics"]),  # type: ignore[arg-type]
        reverse=True,
    )[:count]


def _candidate_from_row(row: dict[str, object]) -> Candidate:
    raw = row["candidate"]
    if not isinstance(raw, dict):
        raise TypeError("checkpoint candidate is not an object")
    raw_additions = cast(list[list[int]], raw["additions"])
    return Candidate(
        candidate_id=str(raw["candidate_id"]),
        kind=str(raw["kind"]),
        generation=int(raw["generation"]),
        additions=tuple((int(cell[0]), int(cell[1])) for cell in raw_additions),
        description=str(raw["description"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bgolly", type=Path, default=DEFAULT_BGOLLY)
    parser.add_argument(
        "--workers", type=int, default=min(12, max(1, os.cpu_count() or 1))
    )
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x151A_66)
    parser.add_argument("--cell-samples", type=int, default=900)
    parser.add_argument("--cell-generations", type=_parse_int_list, default=(1326,))
    parser.add_argument("--cell-margin-x", type=int, default=10)
    parser.add_argument("--cell-margin-y", type=int, default=12)
    parser.add_argument("--skip-cells", action="store_true")
    parser.add_argument(
        "--cell-effective-only",
        action="store_true",
        help="only add cells that can change generation one of Max151",
    )
    parser.add_argument("--spaceship-samples", type=int, default=900)
    parser.add_argument(
        "--spaceship-generations",
        type=_parse_int_list,
        default=tuple(range(1125, 1901, 25)) + (1326,),
    )
    parser.add_argument("--spaceship-margin-x", type=int, default=90)
    parser.add_argument("--spaceship-margin-y", type=int, default=130)
    parser.add_argument("--skip-spaceships", action="store_true")
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="skip augmentation generation and only calibrate timing controls",
    )
    parser.add_argument("--screen-contexts", type=int, default=18)
    parser.add_argument("--refine-contexts", type=int, default=384)
    parser.add_argument("--refine-top", type=int, default=24)
    parser.add_argument("--refine-per-kind", type=int, default=12)
    parser.add_argument("--baseline-refine-top", type=int, default=4)
    parser.add_argument("--exhaustive-top", type=int, default=0)
    parser.add_argument(
        "--exhaustive-batch-size",
        type=int,
        default=1,
        help="causally isolated contexts evolved in each exhaustive bgolly call",
    )
    parser.add_argument(
        "--exhaustive-baselines",
        type=_parse_int_list,
        default=(),
        help="exhaustively enumerate these unmodified-probe generations",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=ROOT / "analysis/level2/max151-augmentation-search.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "analysis/level2/max151-augmentation-report.json",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be positive")
    if not args.bgolly.exists():
        parser.error(f"bgolly not found: {args.bgolly}")
    for name in (
        "cell_samples",
        "spaceship_samples",
        "screen_contexts",
        "refine_contexts",
        "refine_top",
        "refine_per_kind",
        "baseline_refine_top",
        "exhaustive_top",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be nonnegative")
    if args.exhaustive_batch_size < 1:
        parser.error("--exhaustive-batch-size must be positive")

    print(
        f"Max151 base: cells={len(BASE_CELLS)} bounds={BASE_BOUNDS}; "
        f"spaceship phases={ {name: len(phases) for name, phases in SPACESHIP_PHASES.items()} }",
        flush=True,
    )
    model = Max151Model()
    generator = random.Random(args.seed)
    candidates: list[Candidate] = []
    candidates.extend(
        _baseline_candidate(generation)
        for generation in sorted(
            set(args.cell_generations)
            | set(args.spaceship_generations)
            | set(args.exhaustive_baselines)
        )
    )
    if not args.baseline_only:
        if not args.skip_cells:
            candidates.extend(
                _single_cell_candidates(
                    generator,
                    args.cell_samples,
                    args.cell_generations,
                    args.cell_margin_x,
                    args.cell_margin_y,
                    args.cell_effective_only,
                )
            )
        if not args.skip_spaceships:
            candidates.extend(
                _spaceship_candidates(
                    generator,
                    args.spaceship_samples,
                    args.spaceship_generations,
                    args.spaceship_margin_x,
                    args.spaceship_margin_y,
                )
            )
    print(
        f"generated {len(candidates)} candidates "
        f"({sum(candidate.kind == 'cell' for candidate in candidates)} cells, "
        f"{sum(candidate.kind == 'spaceship' for candidate in candidates)} spaceships, "
        f"{sum(candidate.kind == 'baseline' for candidate in candidates)} baselines)",
        flush=True,
    )

    cached = _load_checkpoint(args.checkpoint)
    screen_indices = _sample_context_indices(model, args.screen_contexts, args.seed ^ 0x51)
    screen = _run_stage(
        "screen",
        candidates,
        screen_indices,
        model,
        args.bgolly,
        args.workers,
        1,
        args.checkpoint,
        cached,
    )

    mutation_screen = {
        candidate_id: row
        for candidate_id, row in screen.items()
        if row["candidate"]["kind"] != "baseline"  # type: ignore[index]
    }
    selected_rows = _top_rows(mutation_screen, args.refine_top)
    for kind in ("cell", "spaceship"):
        kind_rows = {
            candidate_id: row
            for candidate_id, row in mutation_screen.items()
            if row["candidate"]["kind"] == kind  # type: ignore[index]
        }
        selected_rows.extend(_top_rows(kind_rows, args.refine_per_kind))

    # Deduplicate the overlapping overall/per-family selections.
    selected_rows = list(
        {
            str(row["candidate"]["candidate_id"]): row  # type: ignore[index]
            for row in selected_rows
        }.values()
    )
    selected_ids = {
        str(row["candidate"]["candidate_id"])  # type: ignore[index]
        for row in selected_rows
    }
    selected_generations = {
        int(row["candidate"]["generation"])  # type: ignore[index]
        for row in selected_rows
    }
    baseline_screen = {
        candidate_id: row
        for candidate_id, row in screen.items()
        if row["candidate"]["kind"] == "baseline"  # type: ignore[index]
    }
    baseline_rows = _top_rows(baseline_screen, args.baseline_refine_top)
    baseline_rows.extend(
        row
        for row in baseline_screen.values()
        if int(row["candidate"]["generation"]) in selected_generations  # type: ignore[index]
    )
    for row in baseline_rows:
        candidate_id = str(row["candidate"]["candidate_id"])  # type: ignore[index]
        if candidate_id not in selected_ids:
            selected_rows.append(row)
            selected_ids.add(candidate_id)
    refine_candidates = [_candidate_from_row(row) for row in selected_rows]
    refine_indices = _sample_context_indices(
        model, args.refine_contexts, args.seed ^ 0xF1E
    )
    refine = _run_stage(
        "refine",
        refine_candidates,
        refine_indices,
        model,
        args.bgolly,
        args.workers,
        1,
        args.checkpoint,
        cached,
    )

    exhaustive: dict[str, dict[str, object]] = {}
    if args.exhaustive_top or args.exhaustive_baselines:
        mutation_refine = {
            candidate_id: row
            for candidate_id, row in refine.items()
            if row["candidate"]["kind"] != "baseline"  # type: ignore[index]
        }
        exhaustive_candidates = [
            _candidate_from_row(row)
            for row in _top_rows(mutation_refine, args.exhaustive_top)
        ]
        # Include the matching unmodified generation(s).
        exhaustive_generations = {candidate.generation for candidate in exhaustive_candidates}
        exhaustive_generations.update(args.exhaustive_baselines)
        exhaustive_candidates.extend(
            _baseline_candidate(generation) for generation in exhaustive_generations
        )
        exhaustive_candidates = list(
            {candidate.candidate_id: candidate for candidate in exhaustive_candidates}.values()
        )
        exhaustive = _run_stage(
            "exhaustive",
            exhaustive_candidates,
            tuple(range(len(model.contexts))),
            model,
            args.bgolly,
            args.workers,
            args.exhaustive_batch_size,
            args.checkpoint,
            cached,
        )

    report = {
        "format": "golduck-max151-augmentation-search",
        "version": 1,
        "seed": args.seed,
        "base_cells": len(BASE_CELLS),
        "base_bounds": BASE_BOUNDS,
        "windows": WINDOWS,
        "candidate_count": len(candidates),
        "screen_contexts": len(screen_indices),
        "refine_contexts": len(refine_indices),
        "top_screen": _top_rows(screen, min(50, len(screen))),
        "top_refine": _top_rows(refine, min(50, len(refine))),
        "exhaustive": _top_rows(exhaustive, len(exhaustive)),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.report}", flush=True)
    print("top refined candidates:", flush=True)
    for row in _top_rows(refine, min(12, len(refine))):
        candidate = row["candidate"]
        metrics = row["metrics"]
        assert isinstance(candidate, dict) and isinstance(metrics, dict)
        window, values = max(
            metrics.items(),
            key=lambda item: (
                float(item[1]["mean_forced_bits"]),
                float(item[1]["entropy_bits"]),
            ),
        )
        print(
            f"  {candidate['candidate_id']:<48} {window:<6} "
            f"forced={values['mean_forced_bits']:.4f} "
            f"entropy={values['entropy_bits']:.4f} "
            f"classes={values['classes']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
