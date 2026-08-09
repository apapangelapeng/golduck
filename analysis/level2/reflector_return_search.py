#!/usr/bin/env python3
"""Discover late Level 2 glider returns with production agent simulations.

The compact reflector used by explore8 is normally sampled at generation
4017.  This experiment samples it at generation 4035, when the original
return is still visible and the useful second return is fully in the viewing
rectangle.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_simulation import agent_simulate_and_score
from golduck.rle import encode_rle, parse_rle, pattern_from_cells
from multirow_fleet_search import secret_for_seed


GLIDER = ((0, 0), (1, 0), (1, 2), (2, 0), (2, 1))
CATALYST5 = ((0, 1), (0, 2), (1, 0), (1, 2), (2, 1))
CATALYST7 = ((0, 2), (0, 3), (1, 1), (1, 3), (2, 1), (3, 0), (3, 1))


@dataclass(frozen=True)
class Observation:
    seed: str
    residue: int
    secret: int
    parity: tuple[int, ...]
    components: tuple[tuple[tuple[int, int], ...], ...]


def add_shape(
    cells: set[tuple[int, int]],
    shape: tuple[tuple[int, int], ...],
    x: int,
    y: int,
) -> None:
    cells.update((x + dx, y + dy) for dx, dy in shape)


def build_pattern(residue: int) -> str:
    cells: set[tuple[int, int]] = set()
    for bit in range(residue, 64, 8):
        add_shape(cells, GLIDER, 107 + 3 * bit, 0)
        add_shape(cells, CATALYST5, 712 + 3 * bit, 7)
        add_shape(cells, CATALYST7, 710 + 3 * bit, 13)
    return encode_rle(pattern_from_cells(cells, 1000, 200))


def decode_cells(rle: str) -> set[tuple[int, int]]:
    pattern = parse_rle(rle, require_header=True)
    return {
        (x, y)
        for y, intervals in pattern.rows.items()
        for start, end in intervals
        for x in range(start, end)
    }


def components(cells: set[tuple[int, int]]) -> tuple[tuple[tuple[int, int], ...], ...]:
    remaining = set(cells)
    result: list[tuple[tuple[int, int], ...]] = []
    while remaining:
        start = remaining.pop()
        found = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dx or dy):
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        found.add(neighbor)
                        queue.append(neighbor)
        result.append(tuple(sorted(found)))
    return tuple(result)


def parity_labels(secret: int) -> tuple[int, ...]:
    generator = random.Random(secret)
    labels = [-1] * 64
    current = -1
    for bit in range(64):
        if (secret >> bit) & 1:
            if bit == 0 or not ((secret >> (bit - 1)) & 1):
                current = generator.randrange(2)
            labels[bit] = current
    return tuple(labels)


def run_seed(
    index: int, patterns: tuple[str, ...], generations: int
) -> tuple[Observation, ...]:
    seed = hashlib.sha256(f"reflector-return-{index}".encode()).hexdigest()[:32]
    secret = secret_for_seed(seed)
    parity = parity_labels(secret)
    started = agent_simulate_and_score({"action": "start", "seed": seed})
    session_id = str(started["session_id"])
    observations: list[Observation] = []
    try:
        for residue, pattern in enumerate(patterns):
            response = agent_simulate_and_score(
                {
                    "action": "run",
                    "session_id": session_id,
                    "level": 2,
                    "pattern": pattern,
                    "generations": generations,
                }
            )
            observations.append(
                Observation(
                    seed,
                    residue,
                    secret,
                    parity,
                    components(decode_cells(str(response["output_rle"]))),
                )
            )
    finally:
        agent_simulate_and_score({"action": "close", "session_id": session_id})
    return tuple(observations)


def normalized_shape(
    component: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], ...]:
    min_x = min(x for x, _ in component)
    min_y = min(y for _, y in component)
    return tuple(sorted((x - min_x, y - min_y) for x, y in component))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=32)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--generations", type=int, default=4035)
    args = parser.parse_args()

    patterns = tuple(build_pattern(residue) for residue in range(8))
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        nested = executor.map(
            lambda index: run_seed(index, patterns, args.generations),
            range(args.seeds),
        )
        observations = [item for group in nested for item in group]

    hits5: dict[tuple[object, ...], Counter[tuple[int, int]]] = defaultdict(Counter)
    hits8: dict[tuple[object, ...], Counter[tuple[int, int]]] = defaultdict(Counter)
    component_count = 0
    for observation in observations:
        for component in observation.components:
            if len(component) != 5:
                continue
            component_count += 1
            min_x = min(x for x, _ in component)
            min_y = min(y for _, y in component)
            shape = normalized_shape(component)
            for bit in range(observation.residue, 64, 8):
                if bit < 5 or bit > 61:
                    continue
                delta_x = min_x - 3 * bit
                if not 200 <= delta_x <= 400:
                    continue
                family = (min_y, delta_x, shape)
                context5 = (observation.secret >> (bit - 2)) & 0x1F
                context8 = (observation.secret >> (bit - 5)) & 0xFF
                parity = observation.parity[bit]
                hits5[family][(context5, parity)] += 1
                hits8[family][(context8, parity)] += 1

    print(
        f"{args.seeds} seeds, {len(observations)} agent runs, "
        f"{component_count} five-cell output components"
    )
    ranked = sorted(
        hits5,
        key=lambda family: (-sum(hits5[family].values()), family[0], family[1]),
    )
    for family in ranked:
        total = sum(hits5[family].values())
        if total < 2:
            continue
        y, delta_x, shape = family
        best5, count5 = hits5[family].most_common(1)[0]
        best8, count8 = hits8[family].most_common(1)[0]
        print(
            f"y={y:>3} dx={delta_x:>3} n={total:>3} "
            f"ctx5/parity=0x{best5[0]:02x}/{best5[1]} "
            f"({count5}/{total}) ctx8/parity=0x{best8[0]:02x}/{best8[1]} "
            f"({count8}/{total}) shape={shape}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
