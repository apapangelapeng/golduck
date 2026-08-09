#!/usr/bin/env python3
"""Evaluate the adaptive Spacefiller exploration artifact on explicit seeds."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from visualizer_eval import evaluate_solution


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", nargs="+", help="128-bit hexadecimal seeds")
    parser.add_argument(
        "--wasm", type=Path, default=ROOT / "solution/explore13.wasm"
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--level2-only",
        action="store_true",
        help="evaluate only Level 2 (faster for decoder experiments)",
    )
    args = parser.parse_args()

    for seed_hex in args.seeds:
        def event(payload: dict[str, object]) -> None:
            if payload["type"] == "parallel_batch_started":
                print(
                    f"{seed_hex} round {payload['round']}: "
                    f"{payload['total']} simulations",
                    flush=True,
                )

        result = evaluate_solution(
            args.wasm,
            bytes.fromhex(seed_hex),
            event,
            parallel_workers=args.workers,
            selected_levels={2} if args.level2_only else None,
        )
        level2 = next(level for level in result["levels"] if level["level"] == 2)
        print(
            f"{seed_hex} score={result['score']:.6f} "
            f"level2_known={level2['known_bits']} "
            f"level2_exact={level2['exact_answer']} "
            f"level2_runs={level2['runs_completed']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
