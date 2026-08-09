# Level 2 four-bits-per-run exploration

## Result

Sixteen Level 2 runs make four deterministic bits per run the natural exact
readout target: `16 * 4 = 64`, followed by the 100,000-point exact-answer
bonus.  The BW55 and BW56 probes do not yet provide that primitive.  They are
sparse event detectors: a hit proves four or five local bits, but a miss is
not itself a four-bit symbol.

Eight numbered experiments were built from `solution/bw55.c`.  The best tested
candidate is `solution/explore8.wasm`.  It combines:

- BW56's compact eight-cell LWSS and 17-cell reflected-glider lanes;
- the BW55 right-reflector predicate in all eight residue classes;
- exact forward/backward constraint propagation over both hits and misses;
- the two physical observations at the right boundary that earlier versions
  discarded;
- guesses only when the relaxed model assigns a value more than 60% posterior
  probability, the scorer's strict break-even point; and
- the earliest validated one-cell reflector marker, at generation 4,017.

This is an expected-score improvement and a useful step toward a four-bit
readout.  It does **not** claim a guaranteed four bits from each run.  Reaching
that stronger goal still requires a probe with 16 distinguishable outcomes
per run, or a family of outcomes that partitions every possible local context
instead of recognizing only rare predicates.

## Score comparison

All results below are complete exact-Life evaluations stored in
`score_history.json` for the same 13 comparison seeds.

| Artifact | Experiment | Mean score | Delta vs BW55 | Mean L2 known bits | Mean L2 guesses |
|---|---|---:|---:|---:|---:|
| `bw55.wasm` | Reference | 2,343,404.294 | -- | 17.231 | 0.000 |
| `bw56.wasm` | Reference | 2,290,257.356 | -53,146.939 | 18.462 | 15.385 |
| `explore1.wasm` | All four LWSS recipes | 2,269,911.341 | -73,492.953 | 12.077 | 0.000 |
| `explore2.wasm` | BW55 events + safe CSP | 2,346,724.964 | +3,320.670 | 17.462 | 0.000 |
| `explore3.wasm` | CSP + calibrated guesses | 2,351,595.280 | +8,190.986 | 17.462 | 4.769 |
| `explore4.wasm` | Reconstructed BW56 compact probes | 2,346,026.852 | +2,622.557 | 17.000 | 14.923 |
| `explore5.wasm` | Compact probes, right predicate everywhere | 2,356,013.979 | +12,609.684 | 17.692 | 4.923 |
| `explore6.wasm` | Full marker at generation 4,025 | 2,356,019.854 | +12,615.560 | 17.692 | 4.923 |
| `explore7.wasm` | First marker cell at generation 4,017 | 2,356,021.378 | +12,617.084 | 17.692 | 4.923 |
| `explore8.wasm` | Add centers 62/63 to inference | **2,362,568.681** | **+19,164.386** | **18.154** | **5.077** |

`explore8.wasm` is also 72,311.325 points above BW56's mean on this comparison
set.  Its Level 2 run profile is 16 runs, at most 136 input cells, and at most
4,017 generations, giving a Level 2 performance score of 923,280.627275.

## Why the experiments converge on `explore8`

The four LWSS alignments were exhaustively characterized over all local
five-bit contexts and both run parities.  Spending all 16 calls on them
(`explore1`) loses information because each alignment fires sparsely.  The
better allocation keeps the two complementary BW55 LWSS predicates and one
reflected-glider predicate.

The important software gain is treating every observed hit **and miss** as a
constraint.  A 256-state dynamic program retains the last four secret bits and
their one-run parity labels.  Forward/backward passes prove bits with only one
feasible value and compute marginals for unresolved bits.  New one-runs branch
over both parity labels, a conservative relaxation of Python's secret-seeded
parity generator; therefore values forced by this model are safe to mark
known.

The compact right reflector works in every residue class and retains BW55's
more useful `context == 0x07, parity == 0` event.  It lowers Level 2's maximum
input population from BW55's 432 cells to 136.  The earliest returned marker
enters the viewing rectangle at generation 4,017.  Finally, appending the two
implicit zero bits beyond bit 63 lets `explore8` consume the already-measured
events at centers 62 and 63; those edge facts sometimes propagate several
positions to the left.

## Build definitions

The common build is:

```sh
/opt/homebrew/opt/llvm/bin/clang --target=wasm32 -O3 -nostdlib \
  DEFINES \
  -Wl,--no-entry -Wl,--export-memory -Wl,--allow-undefined \
  -Wl,--strip-all -Wl,--export-dynamic -I solution solution/bw55.c \
  -o solution/exploreN.wasm
```

Replace `DEFINES` with:

| Artifact | Defines |
|---|---|
| `explore1.wasm` | `-DEXPLORE_ALL_LWSS` |
| `explore2.wasm` | `-DEXPLORE_CSP` |
| `explore3.wasm` | explore2 + `-DEXPLORE_WEIGHTED` |
| `explore4.wasm` | explore3 + `-DEXPLORE_COMPACT_LWSS -DEXPLORE_COMPACT_REFLECTOR` |
| `explore5.wasm` | explore4 + `-DEXPLORE_COMPACT_SNARK` |
| `explore6.wasm` | explore5 + `-DEXPLORE_EARLY_REFLECTOR` |
| `explore7.wasm` | explore6 + `-DEXPLORE_EARLIEST_REFLECTOR` |
| `explore8.wasm` | explore7 + `-DEXPLORE_END_BOUNDARY` |

## Validation

- All eight artifacts pass `wasm-validate` and finalize successfully.
- Every stored evaluation has a matching artifact SHA-256 and `complete`
  status.
- Every Level 2 known mask was correct on the 13 comparison seeds.
- On 128 additional deterministic random seeds, `explore8` had zero incorrect
  known masks.  Against `explore7` on identical cached Life outputs it won 21,
  tied 103, and lost 4, with a mean gain of 5,567.635 points.  The losses came
  only from optional guesses; its aggregate guess accuracy was 69.0%.
- Rebuilding `bw55.c` without exploration defines reproduces the zero-seed
  BW55 score `2,412,031.474975959` and its 22-bit Level 2 known mask.

