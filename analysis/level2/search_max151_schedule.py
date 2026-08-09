#!/usr/bin/env python3
"""Measure fixed and adaptive run schedules for the Max151 Level 2 probe.

The production artifact stores the exhaustive mapping from every legal
12-trit secret context to its returned Max151 observation class.  This tool
reuses that mapping to answer a narrower question: after a fixed prefix of
probes, can one adaptively placed final probe distinguish every exact
CPython-parity candidate?

It deliberately verifies candidates with ``random.Random(secret)`` rather
than only using the relaxed adjacent-trit model.  The reported survivor count
therefore matches the Level 2 secret generator.
"""

from __future__ import annotations

import argparse
import collections
import random
import re
import statistics
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "solution/max151_adaptive8.c"
SECRET_BITS = 64
CONTEXT_BITS = 12


def _c_u8_array(source: str, name: str) -> list[int]:
    match = re.search(
        rf"static const u8 {name}\[[^]]+\] = \{{(.*?)\}};", source, re.S
    )
    if match is None:
        raise ValueError(f"could not find {name} in {SOURCE}")
    return [
        int(token.strip(), 0)
        for token in match.group(1).split(",")
        if token.strip()
    ]


def _lzss(source: list[int], target_length: int) -> list[int]:
    target: list[int] = []
    source_position = 0
    while len(target) < target_length:
        control = source[source_position]
        source_position += 1
        for bit in range(8):
            if len(target) >= target_length:
                break
            if control & (1 << bit):
                target.append(source[source_position])
                source_position += 1
            else:
                offset = source[source_position] | (
                    source[source_position + 1] << 8
                )
                length = source[source_position + 2] + 3
                source_position += 3
                for _ in range(length):
                    target.append(target[-offset])
    return target


def load_context_classes() -> tuple[int, ...]:
    source = SOURCE.read_text(encoding="ascii")
    compressed_values = _c_u8_array(source, "SF2CTXLZVALUE")
    compressed_counts = _c_u8_array(source, "SF2CTXLZCOUNT")
    values = _lzss(compressed_values, 16_694)
    counts = _lzss(compressed_counts, 8_347)
    classes: list[int] = []
    for run, count in enumerate(counts):
        value = values[2 * run] | (values[2 * run + 1] << 8)
        classes.extend([value] * count)
    if len(classes) != 47_321:
        raise ValueError(f"decoded {len(classes)} classes, expected 47321")
    return tuple(classes)


def legal_contexts() -> tuple[tuple[int, ...], ...]:
    result: list[tuple[int, ...]] = []

    def visit(prefix: tuple[int, ...]) -> None:
        if len(prefix) == CONTEXT_BITS:
            result.append(prefix)
            return
        for symbol in range(3):
            if prefix and prefix[-1] and symbol and prefix[-1] != symbol:
                continue
            visit(prefix + (symbol,))

    visit(())
    return tuple(result)


class Max151Model:
    def __init__(self) -> None:
        self.classes = load_context_classes()
        self.contexts = legal_contexts()
        if len(self.contexts) != len(self.classes):
            raise ValueError("context enumeration does not match the decoder table")
        self.rank = {context: index for index, context in enumerate(self.contexts)}
        mutable_masks: dict[int, set[int]] = collections.defaultdict(set)
        for context, observation_class in zip(self.contexts, self.classes):
            mask = sum(bool(symbol) << bit for bit, symbol in enumerate(context))
            mutable_masks[observation_class].add(mask)
        self.binary_masks = {
            observation_class: tuple(sorted(masks))
            for observation_class, masks in mutable_masks.items()
        }

    @staticmethod
    def geometry(secret: int) -> tuple[int, ...]:
        parity_generator = random.Random(secret)
        result: list[int] = []
        previous = 0
        parity = 0
        for bit in range(SECRET_BITS):
            occupied = (secret >> bit) & 1
            if occupied and not previous:
                parity = parity_generator.randrange(2)
            result.append(parity + 1 if occupied else 0)
            previous = occupied
        return tuple(result)

    def label(self, geometry: tuple[int, ...], start: int) -> int:
        context = tuple(
            geometry[bit] if 0 <= bit < SECRET_BITS else 0
            for bit in range(start - 3, start + 9)
        )
        return self.classes[self.rank[context]]

    def transcript(self, secret: int, starts: tuple[int, ...]) -> tuple[int, ...]:
        geometry = self.geometry(secret)
        return tuple(self.label(geometry, start) for start in starts)

    def relaxed_binary_candidates(
        self, labels: tuple[int, ...], starts: tuple[int, ...]
    ) -> set[int]:
        """Join the possible binary contexts into complete 64-bit values."""

        if len(labels) != len(starts) or not starts:
            raise ValueError("labels and starts must be nonempty and equally sized")
        if tuple(sorted(starts)) != starts:
            raise ValueError("starts must be strictly increasing")

        first = starts[0] - 3
        last = first + CONTEXT_BITS - 1
        candidates = {
            mask
            for mask in self.binary_masks[labels[0]]
            if all(
                not ((mask >> offset) & 1)
                for offset in range(CONTEXT_BITS)
                if not 0 <= first + offset < SECRET_BITS
            )
        }

        for start, label in zip(starts[1:], labels[1:]):
            context_start = start - 3
            overlap = last - context_start + 1
            if overlap < 0:
                raise ValueError("the fixed contexts leave an uncovered bit gap")
            shift = context_start - first
            overlap_mask = (1 << overlap) - 1
            local_masks = tuple(
                mask
                for mask in self.binary_masks[label]
                if all(
                    not ((mask >> offset) & 1)
                    for offset in range(CONTEXT_BITS)
                    if not 0 <= context_start + offset < SECRET_BITS
                )
            )
            candidates = {
                candidate | (local_mask << shift)
                for candidate in candidates
                for local_mask in local_masks
                if ((candidate >> shift) & overlap_mask)
                == (local_mask & overlap_mask)
            }
            last = context_start + CONTEXT_BITS - 1

        if first > 0 or last < SECRET_BITS - 1:
            raise ValueError(
                f"schedule covers [{first}, {last}], not every secret bit"
            )
        return {
            (candidate >> -first) & ((1 << SECRET_BITS) - 1)
            if first < 0
            else candidate & ((1 << SECRET_BITS) - 1)
            for candidate in candidates
        }

    def exact_candidates(
        self, labels: tuple[int, ...], starts: tuple[int, ...]
    ) -> set[int]:
        relaxed = self.relaxed_binary_candidates(labels, starts)
        return {
            candidate
            for candidate in relaxed
            if self.transcript(candidate, starts) == labels
        }

    def best_adaptive_start(
        self,
        candidates: set[int],
        allowed_starts: range,
    ) -> tuple[int, int, dict[int, set[int]]]:
        """Return the minimax start, its worst bucket, and all its buckets."""

        geometries = {
            candidate: self.geometry(candidate) for candidate in candidates
        }
        best_start = allowed_starts.start
        best_worst = len(candidates) + 1
        best_buckets: dict[int, set[int]] = {}
        for start in allowed_starts:
            buckets: dict[int, set[int]] = collections.defaultdict(set)
            for candidate, geometry in geometries.items():
                buckets[self.label(geometry, start)].add(candidate)
            worst = max(map(len, buckets.values()))
            if worst < best_worst:
                best_start = start
                best_worst = worst
                best_buckets = dict(buckets)
        return best_start, best_worst, best_buckets


def parse_starts(value: str) -> tuple[int, ...]:
    try:
        starts = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("starts must be comma-separated integers") from error
    if not starts or tuple(sorted(set(starts))) != starts:
        raise argparse.ArgumentTypeError("starts must be strictly increasing")
    return starts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixed", type=parse_starts, default=parse_starts("3,13,23,33,43,55"))
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x151)
    parser.add_argument("--adaptive-min", type=int, default=-3)
    parser.add_argument("--adaptive-max", type=int, default=61)
    args = parser.parse_args()

    started = time.monotonic()
    model = Max151Model()
    generator = random.Random(args.seed)
    exact_counts: list[int] = []
    final_counts: list[int] = []
    chosen_starts: list[int] = []

    for sample in range(args.samples):
        secret = generator.getrandbits(SECRET_BITS)
        labels = model.transcript(secret, args.fixed)
        candidates = model.exact_candidates(labels, args.fixed)
        adaptive_start, worst, buckets = model.best_adaptive_start(
            candidates, range(args.adaptive_min, args.adaptive_max + 1)
        )
        true_label = model.label(model.geometry(secret), adaptive_start)
        survivors = buckets[true_label]
        if secret not in survivors:
            raise AssertionError("true secret was eliminated")
        exact_counts.append(len(candidates))
        final_counts.append(len(survivors))
        chosen_starts.append(adaptive_start)
        print(
            f"{sample:4d} secret=0x{secret:016x} "
            f"after_fixed={len(candidates):5d} adaptive={adaptive_start:3d} "
            f"worst={worst:3d} final={len(survivors):3d}",
            flush=True,
        )

    print(
        f"fixed={args.fixed} samples={args.samples} "
        f"fixed median/max={statistics.median(exact_counts):g}/{max(exact_counts)} "
        f"final unique={sum(count == 1 for count in final_counts)}/{args.samples} "
        f"final median/max={statistics.median(final_counts):g}/{max(final_counts)} "
        f"adaptive starts={dict(sorted(collections.Counter(chosen_starts).items()))} "
        f"elapsed={time.monotonic() - started:.2f}s"
    )


if __name__ == "__main__":
    main()
