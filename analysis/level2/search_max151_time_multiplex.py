#!/usr/bin/env python3
"""Search dense, vertically staggered two-Max Level 2 probes.

For each placement/generation tuple, compare the empty secret with singleton
bits aimed at the first and second Max151 fronts.  A useful multiplexing
candidate must react to both; candidates that react to only one channel have
not reduced the number of physical observations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import random
import subprocess
import tempfile
from pathlib import Path

from search_max151_multiplex import _candidate_rle, _view_signature


BGOLLY = Path("/opt/homebrew/bin/bgolly")


def _evaluate(
    task: tuple[int, int, int, tuple[int, ...]]
) -> tuple[int, int, int, int, int, int, int]:
    bit_delta, y_delta, generation, extra_secrets = task
    signatures: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory(prefix="max151_time_mux_") as directory:
        input_path = Path(directory) / "input.rle"
        output_path = Path(directory) / "output.rle"
        for secret in (0, 1, 1 << bit_delta) + extra_secrets:
            input_path.write_text(
                _candidate_rle(bit_delta, y_delta, secret), encoding="ascii"
            )
            completed = subprocess.run(
                [
                    str(BGOLLY),
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
            signatures.append(
                _view_signature(output_path.read_text(encoding="ascii"))
            )
    baseline, first, second = signatures[:3]
    return (
        bit_delta,
        y_delta,
        generation,
        baseline[0],
        int(first != baseline),
        int(second != baseline),
        len(set(signatures)),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit-deltas", default="24")
    parser.add_argument("--y-min", type=int, default=30)
    parser.add_argument("--y-max", type=int, default=170)
    parser.add_argument("--y-step", type=int, default=10)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument(
        "--random-probes",
        type=int,
        default=0,
        help="also retain composites whose output varies over N fixed random secrets",
    )
    args = parser.parse_args()

    bit_deltas = tuple(int(value) for value in args.bit_deltas.split(","))
    generator = random.Random(0x151C0DE)
    extra_secrets = tuple(
        generator.getrandbits(64) for _ in range(args.random_probes)
    )
    tasks: list[tuple[int, int, int, tuple[int, ...]]] = []
    for bit_delta in bit_deltas:
        for y_delta in range(args.y_min, args.y_max + 1, args.y_step):
            generations = {
                1326 + 2 * y_delta + offset for offset in (-100, 0, 100)
            }
            generations.update(
                1326 + 4 * y_delta + offset for offset in (-100, 0, 100)
            )
            tasks.extend(
                (bit_delta, y_delta, generation, extra_secrets)
                for generation in sorted(generations)
                if 0 <= generation <= 2500
            )

    useful: list[tuple[int, int, int, int, int, int, int]] = []
    one_channel = 0
    with concurrent.futures.ProcessPoolExecutor(args.workers) as executor:
        for row in executor.map(_evaluate, tasks):
            if (row[4] and row[5]) or row[6] > 1:
                useful.append(row)
            elif row[4] or row[5]:
                one_channel += 1

    useful.sort(key=lambda row: (row[0], row[1], row[2]))
    for bit_delta, y_delta, generation, population, first, second, unique in useful:
        print(
            f"bit_delta={bit_delta:2d} y_delta={y_delta:3d} "
            f"generation={generation:4d} population={population:6d} "
            f"first={first} second={second} unique={unique}"
        )
    print(
        f"tested={len(tasks)} both_channels={len(useful)} "
        f"one_channel={one_channel}"
    )


if __name__ == "__main__":
    main()
