#!/usr/bin/env python3
"""Evaluate the exact Spacefiller-2 Level 2 reconstruction algorithm."""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TABLES = (
    ROOT / "analysis/level2/spacefiller2-signatures-y30.json",
    ROOT / "analysis/level2/spacefiller2-signatures-dy1-y30.json",
)


def decode_chunk(code: int) -> tuple[int, int, int, int]:
    symbols = []
    for _ in range(4):
        symbols.append(code % 3)
        code //= 3
    return tuple(symbols)  # type: ignore[return-value]


CHUNKS = tuple(decode_chunk(code) for code in range(81))
@dataclass(frozen=True)
class SignatureTable:
    signature_by_geometry: dict[str, int]
    transitions: dict[int, dict[int, tuple[int, ...]]]

    @classmethod
    def load(cls, path: Path) -> "SignatureTable":
        payload = json.loads(path.read_text(encoding="utf-8"))
        signature_by_geometry = {
            record["geometry"]: int(record["signature"], 16)
            for record in payload["records"]
        }
        mutable: dict[int, dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for record in payload["records"]:
            mutable[int(record["left"])][int(record["signature"], 16)].append(
                int(record["right"])
            )
        transitions = {
            left: {
                signature: tuple(rights)
                for signature, rights in by_signature.items()
            }
            for left, by_signature in mutable.items()
        }
        return cls(signature_by_geometry, transitions)


def secret_geometry(secret: int) -> tuple[int, ...]:
    parity_generator = random.Random(secret)
    geometry = []
    previous = 0
    parity = 0
    for bit in range(64):
        live = (secret >> bit) & 1
        if live and not previous:
            parity = parity_generator.randrange(2)
        geometry.append(0 if not live else parity + 1)
        previous = live
    return tuple(geometry)


def observation_labels(
    geometry: tuple[int, ...],
    tables: tuple[SignatureTable, ...],
    schedule: tuple[int, ...],
    base: int,
) -> tuple[int, ...]:
    labels = []
    for run in range(16):
        start = base + 4 * run
        context = "".join(
            str(geometry[bit] if 0 <= bit < 64 else 0)
            for bit in range(start, start + 8)
        )
        labels.append(tables[schedule[run]].signature_by_geometry[context])
    return tuple(labels)


def geometry_from_path(path: list[int], base: int) -> tuple[int, ...]:
    padded_geometry = []
    for chunk in path:
        padded_geometry.extend(CHUNKS[chunk])
    geometry = padded_geometry[-base : 64 - base]
    assert len(geometry) == 64
    return tuple(geometry)


def identity_matches(secret: int, geometry: tuple[int, ...]) -> bool:
    return secret_geometry(secret) == geometry


def reconstruct(
    labels: tuple[int, ...],
    tables: tuple[SignatureTable, ...],
    schedule: tuple[int, ...],
    base: int,
    wildcard_runs: frozenset[int] = frozenset(),
) -> tuple[int, set[int]]:
    """Return relaxed path count and exact Python-parity survivors."""

    path = [0] * 17
    relaxed_paths = 0
    survivors: set[int] = set()
    initial_chunks = tuple(
        code
        for code, symbols in enumerate(CHUNKS)
        if all(symbols[position] == 0 for position in range(-base))
    )
    final_zero_start = 64 - (base + 64)
    final_chunks = frozenset(
        code
        for code, symbols in enumerate(CHUNKS)
        if all(symbols[position] == 0 for position in range(final_zero_start, 4))
    )

    def visit(run: int, left: int) -> None:
        nonlocal relaxed_paths
        if run == 16:
            if left not in final_chunks:
                return
            relaxed_paths += 1
            geometry = geometry_from_path(path, base)
            secret = sum(
                (symbol != 0) << bit for bit, symbol in enumerate(geometry)
            )
            if identity_matches(secret, geometry):
                survivors.add(secret)
            return

        by_signature = tables[schedule[run]].transitions.get(left, {})
        if run in wildcard_runs:
            rights = tuple(
                right for values in by_signature.values() for right in values
            )
        else:
            rights = by_signature.get(labels[run], ())
        for right in rights:
            path[run + 1] = right
            visit(run + 1, right)

    for left in initial_chunks:
        path[0] = left
        visit(0, left)
    return relaxed_paths, survivors


def parse_schedule(value: str) -> tuple[int, ...]:
    if value == "all0":
        return (0,) * 16
    if value == "all1":
        return (1,) * 16
    if value == "alternate":
        return tuple(run & 1 for run in range(16))
    if value == "quarter":
        return tuple(int(run % 4 == 3) for run in range(16))
    if value == "edges":
        return tuple(int(run in (0, 15)) for run in range(16))
    digits = tuple(int(character) for character in value)
    if len(digits) != 16:
        raise argparse.ArgumentTypeError("schedule must name a preset or have 16 digits")
    return digits


def signature_for_context(
    secret: int, table: SignatureTable, start: int
) -> int:
    geometry = secret_geometry(secret)
    context = "".join(
        str(geometry[bit] if 0 <= bit < 64 else 0)
        for bit in range(start, start + 8)
    )
    return table.signature_by_geometry[context]


def choose_adaptive_start(
    candidates: set[int], table: SignatureTable
) -> tuple[int, int, dict[int, tuple[int, ...]]]:
    """Choose the eight-bit window with the smallest worst output bucket."""

    geometries = {candidate: secret_geometry(candidate) for candidate in candidates}
    signatures: dict[int, tuple[int, ...]] = {}
    for candidate, geometry in geometries.items():
        candidate_labels = []
        for start in range(-7, 64):
            context = "".join(
                str(geometry[bit] if 0 <= bit < 64 else 0)
                for bit in range(start, start + 8)
            )
            candidate_labels.append(table.signature_by_geometry[context])
        signatures[candidate] = tuple(candidate_labels)

    best_start = -7
    best_worst_bucket = len(candidates)
    for start in range(-7, 64):
        buckets: dict[int, int] = defaultdict(int)
        for candidate in candidates:
            buckets[signatures[candidate][start + 7]] += 1
        worst_bucket = max(buckets.values())
        if worst_bucket < best_worst_bucket:
            best_start = start
            best_worst_bucket = worst_bucket
    return best_start, best_worst_bucket, signatures


def choose_adaptive_probe(
    candidates: set[int], tables: tuple[SignatureTable, ...]
) -> tuple[int, int, int, tuple[dict[int, tuple[int, ...]], ...]]:
    """Choose both the probe shape and window with the best worst bucket."""

    signatures_by_table = tuple(
        choose_adaptive_start(candidates, table)[2] for table in tables
    )
    best_table = 0
    best_start = -7
    best_worst_bucket = len(candidates) + 1
    for table_index, signatures in enumerate(signatures_by_table):
        for start in range(-7, 64):
            buckets: dict[int, int] = defaultdict(int)
            for candidate in candidates:
                buckets[signatures[candidate][start + 7]] += 1
            worst_bucket = max(buckets.values())
            if worst_bucket < best_worst_bucket:
                best_table = table_index
                best_start = start
                best_worst_bucket = worst_bucket
    return best_table, best_start, best_worst_bucket, signatures_by_table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x5757)
    parser.add_argument("--schedule", type=parse_schedule, default=parse_schedule("all0"))
    parser.add_argument("--base", type=int, choices=range(-4, 1), default=-2)
    parser.add_argument("--tables", nargs="+", type=Path, default=DEFAULT_TABLES)
    parser.add_argument("--omit", type=int, choices=range(16))
    parser.add_argument("--adaptive", action="store_true")
    parser.add_argument("--adaptive-all-tables", action="store_true")
    args = parser.parse_args()

    tables = tuple(SignatureTable.load(path) for path in args.tables)
    generator = random.Random(args.seed)
    path_counts = []
    missing = []
    ambiguous = []
    started = time.monotonic()
    for _ in range(args.samples):
        secret = generator.getrandbits(64)
        geometry = secret_geometry(secret)
        labels = observation_labels(geometry, tables, args.schedule, args.base)
        wildcard_runs = (
            frozenset({args.omit}) if args.omit is not None else frozenset()
        )
        paths, survivors = reconstruct(
            labels, tables, args.schedule, args.base, wildcard_runs
        )
        if args.adaptive:
            if args.omit is None:
                parser.error("--adaptive requires --omit")
            adaptive_tables = tables if args.adaptive_all_tables else tables[:1]
            table_index, start, _, signatures_by_table = choose_adaptive_probe(
                survivors, adaptive_tables
            )
            candidate_signatures = signatures_by_table[table_index]
            observed = signature_for_context(
                secret, adaptive_tables[table_index], start
            )
            survivors = {
                candidate
                for candidate in survivors
                if candidate_signatures[candidate][start + 7] == observed
            }
        path_counts.append(paths)
        if secret not in survivors:
            missing.append((secret, survivors))
        if len(survivors) != 1:
            ambiguous.append((secret, survivors))

    print(f"samples: {args.samples}")
    print(f"missing true secret: {len(missing)}")
    print(f"non-unique exact survivors: {len(ambiguous)}")
    print(
        "relaxed paths min/median/mean/max: "
        f"{min(path_counts)}/{statistics.median(path_counts):g}/"
        f"{statistics.mean(path_counts):.3f}/{max(path_counts)}"
    )
    print(f"elapsed seconds: {time.monotonic() - started:.3f}")
    for secret, survivors in ambiguous[:10]:
        values = ", ".join(f"0x{value:016x}" for value in sorted(survivors))
        print(f"ambiguous 0x{secret:016x}: {values}")


if __name__ == "__main__":
    main()
