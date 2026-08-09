# BW55 scoring, mechanism, and improvement guide

This document explains how `solution/bw55.wasm` is scored, how the checked-in
`solution/bw55.c` obtains its answers, and which changes are most likely to
improve its score. It is intended as a source-linked handoff for future agents.

The exact scoring claims below come from the production scorer in
[`golduck/sim.py`](../../golduck/sim.py). The solution-mechanism claims come
from [`solution/bw55.c`](../../solution/bw55.c),
[`solution/l0_table.h`](../../solution/l0_table.h), and the level definitions.
The numerical reference run was executed locally with the checked-in Wasm and
the visualizer's exact B3/S23 engine, which delegates scoring to the production
scorer.

## Executive summary

- The total is the sum of independently scored submitted levels 0, 1, and 2.
- A level first receives a **performance score** near 900,000--1,000,000 based
  on its number of runs, the largest input live-cell count in any run, and the
  largest generation count in any run.
- The performance score is then multiplied by answer weight. Proven
  (`known_mask`) bits are valuable, but **one wrong proven bit zeros the entire
  known-bit weight**. Guessed bits are scored individually and have a 60%
  break-even accuracy threshold.
- A correct 64-bit `known_mask` earns an additional 100,000 exact-answer bonus.
- BW55 reliably targets exact answers on levels 0 and 1. Its level 2 score is
  partial and seed-dependent: if it proves `K` correct bits, level 2 contributes
  `920,932.489451 * K / 64`, unless `K = 64`, when another 100,000 is added.
- At BW55's current level 2 cost, one additional proven bit is worth about
  **14,389.57 points**. That dwarfs small cell, generation, or run-count
  optimizations.
- Costs use the **maximum** live-cell and generation counts across runs, not
  totals or averages. Current low-density runs therefore have substantial
  uncharged packing headroom.

## 1. Exact scoring formula

### 1.1 Statistics recorded by each accepted run

For every call to `run(level, pattern, generations)`, the scorer records:

- `N_i`: the number of live cells in the contestant's input RLE at generation
  zero;
- `G_i`: the requested generation count.

It does **not** score the evolved population, output population, RLE byte size,
declared RLE bounding box, Wasm runtime, or total cells across runs. Runtime and
resource limits are hard pass/fail constraints, not point deductions.

For a level, define:

```text
R     = number of accepted runs
A     = fixed contestant rectangle width * height
Nmax  = max input live-cell count over the runs, or 0 with no runs
Gmax  = max requested generations over the runs, or 0 with no runs
Gcap  = the level's maximum permitted generations
```

The scorer actually takes the maximum of each logarithmic ratio. Since log is
monotone, this is equivalent to using `Nmax` and `Gmax` for valid inputs.

### 1.2 Performance score

```text
run_bonus = 100,000 / (max(R, 1) + 1)

density_ratio = clamp01(log(Nmax + 1) / log(A + 1))
density_bonus = 25,000 * (1 - density_ratio)

generation_ratio = clamp01(log(Gmax + 1) / log(Gcap + 1))
generation_bonus = 25,000 * (1 - generation_ratio)

P = 900,000 + run_bonus + density_bonus + generation_bonus
```

Important consequences:

1. Zero and one run receive the same 50,000 run bonus.
2. Density and generation costs are logarithmic.
3. Only the worst run in each category matters. The max-cell run and max-gen
   run do not need to be the same run.
4. Adding cells to a run cannot change the score while it remains at or below
   the existing `Nmax`. Similarly, increasing a run to at most the existing
   `Gmax` is free from a scoring perspective.
5. An expanding seed or methuselah is attractive: only its generation-zero
   live cells are charged.

### 1.3 Answer weight

Let:

```text
wrong       = submitted_value XOR secret
K           = popcount(known_mask)
C           = correctly guessed bits in guess_mask
W           = wrongly guessed bits in guess_mask
known_good  = known_mask is nonzero AND (wrong & known_mask) == 0
```

The masks must be disjoint. Bits outside both masks are ignored.

```text
known_weight = (K / 64) if known_good else 0
guess_weight = (0.2*C - 0.3*W) / 64
answer_weight = known_weight + guess_weight
```

The source writes the guess expression as
`0.5 * (0.4*C - 0.6*W) / 64`; the expression above is identical.

The final level score is:

```text
exact = known_good AND known_mask == 0xffffffffffffffff

level_score = P * answer_weight + (100,000 if exact else 0)
```

There is no floor at zero. Bad guesses can make a level score negative.

The critical asymmetry is:

- each correct guessed bit contributes `+0.2*P/64`;
- each wrong guessed bit contributes `-0.3*P/64`;
- a guessed bit is positive in expectation only when its calibrated probability
  of correctness is greater than 60%;
- one wrong bit in `known_mask` removes **all** `K/64` known weight, rather than
  only the wrong bit's share.

### 1.4 Final aggregation

`finalize()` scores only levels that were submitted and sums their floating
point level scores:

```text
total_score = sum(score of each submitted level)
```

An unsubmitted level contributes zero. A level permits only one submission,
and no further runs are accepted for it after submission.

## 2. BW55's fixed performance breakdown

BW55 makes 33 total runs: 1 on level 0, 16 on level 1, and 16 on level 2.
The scorer uses the following maxima.

| Level | Runs | Run shapes | `Nmax` | `Gmax` | Area `A` | `Gcap` |
|---|---:|---|---:|---:|---:|---:|
| 0 | 1 | 1 x 55-cell seed | 55 | 1,000 | 673 x 30 = 20,190 | 10,000 |
| 1 | 16 | 16 x 155-cell column probes | 155 | 2,442 | 1,000 x 200 = 200,000 | 10,000 |
| 2 | 16 | 8 x 135-cell LWSS probes; 4 x 432-cell and 4 x 378-cell Snark probes | 432 | 4,004 | 1,000 x 200 = 200,000 | 10,000 |

Substitution into the exact formula gives:

| Score component | Level 0 | Level 1 | Level 2 |
|---|---:|---:|---:|
| Base | 900,000.000000 | 900,000.000000 | 900,000.000000 |
| Run bonus | 50,000.000000 | 5,882.352941 | 5,882.352941 |
| Density bonus | 14,848.293044 | 14,657.086916 | 12,566.157810 |
| Generation bonus | 6,247.490608 | 3,825.708217 | 2,483.978700 |
| Performance score `P` | **971,095.783653** | **924,365.148074** | **920,932.489451** |
| Normal answer weight | 1.0 | 1.0 | `K/64` |
| Exact bonus | 100,000 | 100,000 | 100,000 only if `K=64` |
| Normal level score | **1,071,095.783653** | **1,024,365.148074** | **14,389.570148 x K** |

Levels 0 and 1 therefore provide a fixed intended subtotal of:

```text
2,095,460.931727
```

If level 2's known mask is correct and contains fewer than 64 bits, BW55's
total is:

```text
BW55 total = 2,095,460.931727 + 14,389.570148 * K
```

At the existing run costs, exact answers on all three levels would total:

```text
3,116,393.421178
```

This is the exact-answer ceiling at BW55's current run cost, not a universal
maximum for every possible solution.

## 3. How BW55 obtains the answers

### 3.1 Level 0: 55 charged cells, 64 decoded bits

Level 0 encodes each set secret bit as a left-down glider, with the 64 bit
lanes spaced ten cells apart. BW55 submits one 55-cell contestant pattern at
the minimum permitted 1,000 generations.

`level0_pattern()` constructs eleven identical five-cell seeds at
`x = 29 + 60*j`. Each seed evolves for 175 generations into much larger
period-2 ash. The ash interacts with six nearby secret-glider corridors, while
only the original five cells per band are charged. The source explicitly calls
this the "55-cell level 0" pattern: the input has 11 x 5 = 55 live cells.

After 1,000 generations, `decode_level0_bands()`:

1. partitions the returned viewing window into eleven x-bands;
2. FNV-hashes the live-cell coordinates in each band;
3. looks up each hash in `l0_hash`;
4. decodes ten 6-bit chunks and one 4-bit chunk, totaling 64 bits.

BW55 submits the decoded word with a full known mask and no guess mask. The
lookup data is in [`solution/l0_table.h`](../../solution/l0_table.h).

Scoring insight: the sprawling evolved ash is free. Only the 55 initial cells
matter to density. The run already uses level 0's minimum generation count, so
that part cannot be reduced.

Reliability warning: unlike level 1, a missing level 0 table match silently
leaves the decoded chunk as zero while BW55 still submits a full known mask.
Any such false decode would zero the entire known weight and remove the exact
bonus. Changes to the geometry or tables need exhaustive or very broad
cross-seed validation.

### 3.2 Level 1: isolate and hash one four-bit column per run

Level 1 lays out the secret as four rows by sixteen columns of stable 2x2
blocks. BW55 uses one run per column.

`build_level1_pattern(column)` contains:

- two 10-cell detectors: 20 cells;
- fifteen 9-cell MWSSes that peel away all non-target columns: 135 cells;
- total: 155 live cells in every run.

At generation 2,442, the target column produces one of sixteen output
signatures. `hash_output()` canonicalizes the x coordinate by subtracting
`4*column`, hashes the output cells, and `decode_nibble()` maps the hash to the
column's four secret bits. Sixteen runs recover all sixteen nibbles.

If a signature does not match, BW55 omits that entire column from
`decoded_columns`; it does not falsely claim those four bits as known. Under
normal operation every column decodes, producing a full known mask, exact
answer, and 100,000 bonus.

### 3.3 Level 2: event-driven partial proofs

Level 2 is the source of BW55's seed-to-seed score variation. Its 64 bits form
a two-cell-high strip. Consecutive runs of one-bits also receive a
secret-seeded diagonal parity, so local collision behavior contains both bit
context and run-parity information.

BW55 uses all 16 permitted runs and two detector families.

#### Eight LWSS runs

The code uses active recipes 0 and 1, each at four bit-position residues. Each
run packs fifteen 9-cell upward LWSS probes, for 135 cells, and evolves for
2,600 generations. A recognized downward-LWSS event asserts one of these
four-bit facts, shifted to the probed position:

| Active recipe | Known mask | Value | Meaning on an event |
|---|---:|---:|---|
| 0 | `0x1e` | `0x16` | four of the local five context bits are known |
| 1 | `0x1e` | `0x1a` | a different four-bit local context is known |

The four residues cover probe centers 2 through 61. The fifth context bit is
deliberately omitted because each alignment's two firing contexts differ at
that bit.

Recipes 2 and 3 are defined in the source (`0x0f/0x0d` and `0x0f/0x0b`) but
`run_entry()` loops only over recipes 0 and 1, so recipes 2 and 3 currently add
no information and no score.

#### Eight Snark-reflected glider runs

The next eight runs cover the same center range using residues modulo eight.
Each packed probe consists of a 5-cell north-east glider plus the 49-cell
stable portion of a Snark, or 54 cells per lane.

- residues 2--5 contain eight lanes: `8 x 54 = 432` cells per run;
- residues 6--9 contain seven lanes: `7 x 54 = 378` cells per run;
- all evolve for 4,004 generations.

A complete five-cell marker in the returned output identifies local context
`0x07` (described by the source as `11100`, parity 0) and asserts all five bits
with mask `0x1f` and value `0x07`.

#### Submission

Every detected fact is ORed into `known2` and `secret2`. Overlapping facts do
not count twice; `K` is the popcount of their union. BW55 submits:

```text
value      = secret2
known_mask = known2
guess_mask = 0
```

Consequently, a correct result scores exactly `P2 * K/64`. There is no guess
credit, and there is normally no level 2 exact bonus.

The large `prefix_hashes` table, `decode_prefix()`, `decode_suffix()`, and the
`north_west_glider` definition are not called by `run_entry()` and should not
be included when reasoning about the active solution.

## 4. Reproducible reference score

The checked-in `bw55.wasm` was evaluated with seed:

```text
00000000000000000000000000000000
```

The complete result was:

| Level | Secret | Submission | Known mask | Known bits | Score |
|---|---|---|---|---:|---:|
| 0 | `0x66505af533720487` | same as secret | `0xffffffffffffffff` | 64 | 1,071,095.783653 |
| 1 | `0x0f15849e3b11b5d0` | same as secret | `0xffffffffffffffff` | 64 | 1,024,365.148074 |
| 2 | `0xa44617d091f35d67` | `0x000000d001c05967` | `0x000000f007c079ff` | 22 | 316,570.543249 |
| **Total** |  |  |  |  | **2,412,031.474976** |

The call profile was 33 `run` calls, 3 `submit` calls, 1 `finalize` call, and no
`get_rand` calls.

This reference reconciles exactly:

```text
Level 2 = 920,932.489451184 * 22 / 64
        = 316,570.543248845

Total   = 1,071,095.783652827
        + 1,024,365.148074289
        +   316,570.543248845
        = 2,412,031.474975959
```

## 5. Expected level 2 variability

This subsection is an engineering estimate, not part of the scorer contract.
It helps set improvement priorities.

A 300,000-secret Monte Carlo emulated the local context predicates and the
`random.Random(secret)` run parity used by level 2. The inferred predicates
were checked against the full zero-seed execution above: the emulation produced
the same 22-bit known mask and value.

The estimate sampled secrets directly because the HMAC-derived 64-bit level
secret is pseudorandom. It used these inferred firing rules:

```python
import random


def emulated_known_mask(secret):
    parity = [None] * 64
    parity_rng = random.Random(secret)
    previous_was_one = False
    for bit in range(64):
        is_one = bool((secret >> bit) & 1)
        if is_one:
            if not previous_was_one:
                run_parity = parity_rng.randrange(2)
            parity[bit] = run_parity
        previous_was_one = is_one

    known = 0
    value = 0
    for center in range(2, 62):
        context = (secret >> (center - 2)) & 0x1f

        # Active LWSS recipe 0.
        if (context & 0x1e) == 0x16 and parity[center] == 1:
            known |= 0x1e << (center - 2)
            value |= 0x16 << (center - 2)

        # Active LWSS recipe 1.
        if (context & 0x1e) == 0x1a and parity[center + 1] == 0:
            known |= 0x1e << (center - 2)
            value |= 0x1a << (center - 2)

        # Snark-reflected glider detector.
        if context == 0x07 and parity[center] == 0:
            known |= 0x1f << (center - 2)
            value |= 0x07 << (center - 2)

    return known, value
```

The Monte Carlo driver used `random.Random(0xB055)` and drew 300,000 values
with `getrandbits(64)`. These event predicates are an inference about BW55's
physical detector behavior, not an API guarantee; validate them against more
full Life runs before using the estimate as a regression oracle.

Results:

| Statistic | Proven bits `K` |
|---|---:|
| Mean | 17.4097 |
| 1st percentile | 4 |
| 5th percentile | 5 |
| 25th percentile | 12 |
| Median | 17 |
| 75th percentile | 22 |
| 95th percentile | 30 |
| 99th percentile | 35 |
| Observed sample range | 0--53 |

No 64-bit exact result occurred in those 300,000 samples. The estimated mean
scores, assuming levels 0 and 1 remain exact, were:

```text
mean level 2 score ~=   250,518.39
mean total score   ~= 2,345,979.32
```

Use production-engine multi-seed evaluation before treating this distribution
as definitive. Packed-probe interactions or an incorrectly inferred event
predicate would affect the estimate, although they do not alter the exact
scoring formula.

## 6. Highest-value improvement directions

### Priority 1: prove more level 2 bits without false positives

At the existing level 2 performance score:

```text
one additional correct known bit = 920,932.489451 / 64
                                 = 14,389.570148 points
```

This should be the primary objective. Candidate directions include:

- activate or repurpose the currently unused recipes 2 and 3;
- find detector alignments for more local contexts or both parity cases;
- combine compatible detector families in the same run;
- make later runs adaptive to facts learned from earlier outputs;
- replace redundant probes with probes targeting contexts not covered by the
  current three event classes;
- seek a general readout that closes the remaining bits and reaches the
  additional 100,000 exact-answer bonus.

Correctness matters more than raw coverage. With no guess mask, one false
positive in `known2` makes the entire level 2 score zero. New event signatures
must be tested for false positives across all 32 local bit contexts, relevant
parities, packed-lane interactions, edges, and many complete 64-bit secrets.

### Priority 2: exploit max-cost headroom

Level 2 already sets `Nmax = 432` and `Gmax = 4004`.

- Each of the eight 135-cell LWSS runs can accept as many as 297 additional
  generation-zero live cells without changing the density score, provided the
  run remains at or below 432 cells.
- Each of the four 378-cell Snark runs has 54 cells of similar headroom.
- The 2,600-generation LWSS runs can be increased as high as 4,004 generations
  without changing the generation score.
- All 16 run slots are already used, so extra information must be packed into,
  combined with, or substituted for existing runs rather than added as run 17.

Geometry and collision interference, not scoring cost, are the limiting
factors inside that headroom.

### Priority 3: use uncertainty in the correct mask class

Do not place a bit in `known_mask` unless it is effectively proven. A single
mistake destroys all known credit for that level.

If a new method produces calibrated but non-certain information, put those bits
in the disjoint `guess_mask`. At level 2's current `P`:

```text
correct guessed bit: +2,877.914030
wrong guessed bit:   -4,316.871044
50/50 random guess:    -719.478507 expected
```

The threshold is strictly above 60% correctness. Because the secrets are
derived pseudorandomly, guessing an otherwise unknown bit from its prior alone
is harmful.

### Priority 4: preserve exactness on levels 0 and 1

Efficiency gains on the exact levels are small compared with losing the exact
bonus or known weight.

- Omitting even one otherwise correct level 1 bit would lose about 114,443
  points: the 100,000 exact bonus plus one sixty-fourth of performance.
- A false full-known level 0 answer loses its entire 1,071,096-point
  contribution.
- A failed four-bit level 1 column, safely omitted from the known mask, loses
  about 157,773 points.

Any cell-count or generation optimization must retain exact decoding across
the full secret space.

### Priority 5: optimize efficiency after coverage

The approximate gain from reducing the current maximum by one is:

| Change | Level 0 gain | Level 1 gain | Level 2 performance gain |
|---|---:|---:|---:|
| One fewer max input cell | 45.4416 | 13.1715 | 4.7356 |
| One fewer max generation | 2.7130 | 1.1113 | 0.6778 |

Level 0 cannot actually reduce generations below its current 1,000 minimum.
Level 2's performance gain is additionally multiplied by `K/64`; at `K=22`,
removing one max input cell is worth only about 1.63 final points.

Reducing a 16-run level to 15 runs raises its performance score by only
367.6471 before answer weighting. Reducing from 16 to 8 raises it by 5,228.7582.
Either is a bad trade if it loses even one proven level 2 bit, and reducing
level 1 runs is especially unattractive if it sacrifices exactness.

## 7. Validation checklist for a successor

For every candidate change:

1. Record, per level, `R`, every run's live-cell count, every generation count,
   `Nmax`, and `Gmax`.
2. Recompute `P` with the production formula; do not use total cells or total
   generations.
3. Record submitted value, known mask, and guess mask.
4. In an offline oracle test, assert
   `(submitted_value ^ secret) & known_mask == 0` for every tested seed.
5. Count known, correct-guessed, and wrong-guessed bits and reconcile the score
   exactly.
6. Test local probes exhaustively over all relevant local contexts and parity
   states before packing them.
7. Then test packed patterns over many complete random secrets to catch
   cross-lane debris, edge behavior, and hash/signature collisions.
8. Run the checked-in Wasm through the production `bgolly` path when possible;
   use the portable visualizer engine for development and score diagnostics.
9. Verify the solution still calls `finalize()` and stays inside hard run,
   canvas, corner-integrity, CPU, memory, Wasm-fuel, and wall-clock limits.
10. Compare both mean score and lower-tail score. A high average is not enough
    if rare false known bits zero an entire level.

## 8. Minimal score calculator

This mirrors the relevant functions in `golduck/sim.py` and is useful for
checking a trace:

```python
import math


def performance_score(runs, live_cells, generations, area, generation_cap):
    run_bonus = 100_000.0 / (max(runs, 1) + 1)
    nmax = max(live_cells, default=0)
    gmax = max(generations, default=0)
    density_bonus = 25_000.0 * (
        1.0 - min(1.0, max(0.0, math.log(nmax + 1) / math.log(area + 1)))
    )
    generation_bonus = 25_000.0 * (
        1.0
        - min(
            1.0,
            max(0.0, math.log(gmax + 1) / math.log(generation_cap + 1)),
        )
    )
    return 900_000.0 + run_bonus + density_bonus + generation_bonus


def answer_score(performance, secret, value, known_mask, guess_mask):
    wrong = value ^ secret
    known_good = bool(known_mask) and not (wrong & known_mask)
    known_weight = (known_mask.bit_count() / 64) if known_good else 0.0
    correct_guesses = ((~wrong) & guess_mask & ((1 << 64) - 1)).bit_count()
    wrong_guesses = (wrong & guess_mask).bit_count()
    guess_weight = (0.2 * correct_guesses - 0.3 * wrong_guesses) / 64
    exact_bonus = (
        100_000.0
        if known_good and known_mask == (1 << 64) - 1
        else 0.0
    )
    return performance * (known_weight + guess_weight) + exact_bonus
```

## 9. Source map

- [`golduck/sim.py`](../../golduck/sim.py): authoritative run validation,
  statistic collection, submission rules, `_score_level()`, and
  `_score_submission()`.
- [`golduck/level0.py`](../../golduck/level0.py): level 0 geometry, glider secret,
  generation range, and one-run limit.
- [`golduck/level1.py`](../../golduck/level1.py): level 1 block grid, geometry,
  and limits.
- [`golduck/level2.py`](../../golduck/level2.py): level 2 strip encoding,
  secret-seeded run parity, geometry, and limits.
- [`solution/bw55.c`](../../solution/bw55.c): active solution construction,
  decoding, masks, and host-call sequence.
- [`solution/l0_table.h`](../../solution/l0_table.h): level 0 band ranges and
  exhaustive per-band signatures.
- [`visualizer_eval.py`](../../visualizer_eval.py): observable score breakdown
  and portable B3/S23 execution; it imports the production scoring functions.
- [`runner_core.py`](../../runner_core.py): Wasm ABI and hard execution limits.
- [`tests/test_visualizer.py`](../../tests/test_visualizer.py): score-breakdown
  reconciliation and visualizer checks.
