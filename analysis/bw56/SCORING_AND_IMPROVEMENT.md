# BW56 scoring, mechanism, and improvement guide

This document explains how BW56 is scored, what the stripped Wasm does, how
its `known_mask` and `guess_mask` affect the result, and where a successor is
most likely to gain points.

The most important boundary is:

> **BW56 chooses the submitted value and masks. The scoring system, not the
> Wasm, compares them with the secret and calculates the score.**

The authoritative scorer is [`golduck/sim.py`](../../golduck/sim.py). The
artifact is [`solution/bw56.wasm`](../../solution/bw56.wasm), packaged in
[`solution/bw56.json`](../../solution/bw56.json). Its SHA-256 is:

```text
601d87e0b11d2029a3feecfb8728c7d30395f365f3fecb159d9b6a652b03cc75
```

The original source is unavailable. [`solution/bw56.c`](../../solution/bw56.c)
and [`solution/bw56.h`](../../solution/bw56.h) are exact WABT `wasm2c`
translations of the stripped binary, not recovered original C. Semantic names
and intent are therefore reverse-engineered. The existing
[`BW56_REVERSE_ENGINEERING.md`](../../solution/BW56_REVERSE_ENGINEERING.md)
contains the initial artifact notes.

## Executive summary

- BW56 performs 33 runs: 1 on level 0, 16 on level 1, and 16 on level 2.
- Levels 0 and 1 are intended to be exact. Together they contribute a fixed
  **2,095,523.148680** points when successful.
- Level 2 has a fixed performance score of **923,254.408339**, but its final
  score varies with its proven and guessed bits.
- For a correct Level 2 known mask with fewer than 64 bits:

  ```text
  Level 2 score = 14,425.850130*K
                +  2,885.170026*C
                -  4,327.755039*W
  ```

  where `K` is the number of correct known bits, `C` is the number of correct
  guessed bits, and `W` is the number of wrong guessed bits.
- On the all-zero challenge seed, BW56 has `K=26`, `C=13`, and `W=5`, giving a
  total score of **2,486,463.687211**.
- On that seed it beats BW55 by **74,432.212235** points, mostly through better
  Level 2 information rather than efficiency bonuses.
- BW56's main new mechanism is a constraint/probability model: detector events
  establish known bits, logical propagation proves more bits, and a weighted
  dynamic program decides which unresolved bits are worth guessing.
- Guess calibration is the highest-leverage software-only improvement. The
  scorer's theoretical break-even correctness is 60%, while BW56 uses inferred
  probability thresholds as low as 57.9% for guessed ones and roughly
  58--65% for guessed zeros.

## 1. What comes from the Wasm and what comes from the scorer

BW56 calls the host ABI:

```text
submit(level, value, known_mask, guess_mask)
```

The Wasm constructs all three 64-bit arguments:

- `value`: BW56's proposed bit values;
- `known_mask`: positions BW56 claims are certainly correct;
- `guess_mask`: disjoint positions BW56 wants scored as uncertain guesses.

The host stores that declaration. During `finalize()`, the scoring system:

1. derives the hidden 64-bit secret from the challenge seed;
2. compares `value` with that secret;
3. checks every known bit;
4. counts correct and wrong guessed bits;
5. computes the performance and answer weights;
6. sums the three submitted level scores.

BW56 cannot directly set or report its own score. It can only choose its runs,
submission values, and masks.

## 2. Exact scoring formula

This formula is shared with BW55 and every other solution evaluated by this
runner.

For each level, define:

```text
R     = number of accepted runs
A     = fixed contestant rectangle area
Nmax  = largest generation-zero input live-cell count across runs
Gmax  = largest requested generation count across runs
Gcap  = maximum generations permitted by the level
```

The performance score is:

```text
run_bonus        = 100,000 / (max(R, 1) + 1)
density_bonus    = 25,000 * (1 - log(Nmax + 1) / log(A + 1))
generation_bonus = 25,000 * (1 - log(Gmax + 1) / log(Gcap + 1))

P = 900,000 + run_bonus + density_bonus + generation_bonus
```

The implementation clamps the two ratios into `[0, 1]`. Valid BW56 inputs are
already within that interval.

Only the maximum input cell count and maximum generations matter. Total cells,
total generations, evolved population, output population, RLE byte size, and
runtime are not point costs. Runtime and resources are hard pass/fail limits.

For the submitted answer, define:

```text
wrong       = value XOR secret
K           = popcount(known_mask)
C           = correct bits selected by guess_mask
W           = wrong bits selected by guess_mask
known_good  = known_mask != 0 AND (wrong & known_mask) == 0
```

Then:

```text
known_weight = K/64 if known_good else 0
guess_weight = (0.2*C - 0.3*W)/64

exact = known_good AND known_mask == 0xffffffffffffffff

level_score = P * (known_weight + guess_weight)
            + (100,000 if exact else 0)
```

Consequences:

- one wrong known bit zeros the entire known contribution;
- known bits are otherwise worth full weight individually;
- guesses are evaluated bit by bit;
- a correct guess earns `+0.2/64` of `P`;
- a wrong guess costs `-0.3/64` of `P`;
- guessing is positive in expectation only above 60% correctness;
- a full correct guess mask does not earn the exact bonus—the full mask must be
  `known_mask`;
- masks cannot overlap, and unmasked bits are ignored;
- scores are not floored at zero.

## 3. BW56's fixed run costs

The stored full evaluation traces in
[`score_history.json`](../../score_history.json) expose every input RLE and its
scored statistics.

| Level | Runs | Run profile | `Nmax` | `Gmax` | Area `A` | `Gcap` |
|---|---:|---|---:|---:|---:|---:|
| 0 | 1 | 1 x 55 cells at 1,000 generations | 55 | 1,000 | 673 x 30 = 20,190 | 10,000 |
| 1 | 16 | 16 x 153 cells at 2,410 generations | 153 | 2,410 | 1,000 x 200 = 200,000 | 10,000 |
| 2 | 16 | 6 x 120, 2 x 128, and 8 x 136 cells | 136 | 4,056 | 1,000 x 200 = 200,000 | 10,000 |

The first eight Level 2 runs use 2,600 generations. The final eight use 4,056,
so the latter establish `Gmax`.

Substituting those values into the scorer gives:

| Component | Level 0 | Level 1 | Level 2 |
|---|---:|---:|---:|
| Base | 900,000.000000 | 900,000.000000 | 900,000.000000 |
| Run bonus | 50,000.000000 | 5,882.352941 | 5,882.352941 |
| Density bonus | 14,848.293044 | 14,683.515154 | 14,923.091863 |
| Generation bonus | 6,247.490608 | 3,861.496932 | 2,448.963534 |
| Performance `P` | **971,095.783653** | **924,427.365027** | **923,254.408339** |
| Exact score | **1,071,095.783653** | **1,024,427.365027** | **1,023,254.408339** |

When levels 0 and 1 remain exact, their subtotal is:

```text
2,095,523.148680
```

BW56's exact-answer ceiling at its current run cost is:

```text
3,118,777.557019
```

That is not a universal maximum; it is `P + 100,000` for each level using
BW56's current costs.

## 4. Per-level solution mechanism

The run profile and host calls below are verified directly from the binary and
evaluation traces. The semantic descriptions are reverse-engineered because
the binary is stripped.

### 4.1 Level 0: the same 55-cell band decoder

Level 0 encodes each set secret bit as a glider in one of 64 lanes. BW56 uses
one 55-cell input at the minimum 1,000 generations.

The pattern consists of eleven five-cell seeds. Their low-cost initial forms
expand into larger ash, interact with nearby secret-glider bands, and produce
output signatures. The Wasm hashes each band with FNV-1a and uses embedded
lookup tables to recover ten six-bit groups plus one four-bit group.

It submits all 64 decoded bits in `known_mask` and makes no guesses. Both stored
full-seed traces decode Level 0 exactly.

As with BW55, only the 55 input cells are charged. The much larger evolved ash
is free from a scoring perspective.

### 4.2 Level 1: lower-cost column isolation

Level 1 is a four-row by sixteen-column grid of stable 2x2 blocks. BW56 uses
one run per column. Each 153-cell pattern uses moving spaceships to eliminate
the other columns and tuned detector geometry to encode the surviving
four-bit column into the returned view.

Compared with BW55, BW56 reduces every run from 155 to 153 input cells and from
2,442 to 2,410 generations. It canonicalizes and FNV-hashes the output, maps
the signature to one nibble, and repeats for all sixteen columns.

Reverse-engineered correction tables handle edge-tuned geometry for columns
0, 1, and 15. Successfully decoded columns are repeated into the four
corresponding rows of the known mask. The two stored full traces decode all 16
columns, submit a full known mask, and receive the exact bonus.

### 4.3 Level 2: two detector families plus inference

Level 2's secret is a two-cell-high strip whose behavior depends on local bit
contexts and a secret-seeded parity assigned to runs of one-bits. BW56 uses all
16 permitted runs.

#### First eight runs: compact spaceship probes

- Each run evolves for 2,600 generations.
- Six runs contain fifteen 8-cell moving patterns: 120 cells.
- Two runs contain sixteen such patterns: 128 cells.
- Residue offsets pack probes across candidate bit positions.
- Returned patterns encode one family of local context/parity events.

The binary embeds four local mask/value recipes corresponding to the familiar
`0x1e/0x16`, `0x1e/0x1a`, `0x0f/0x0d`, and `0x0f/0x0b` context facts. Unlike
BW55's active path, BW56's observation/inference machinery incorporates the
complementary event information rather than relying only on BW55's first two
direct assertions.

#### Final eight runs: complementary 17-cell lanes

- Each run evolves for 4,056 generations.
- Each contains eight lanes of 17 cells: 136 cells total.
- A lane consists of two 5-cell moving motifs and one 7-cell detector motif.
- Returned markers provide a second, complementary event family.

These runs replace BW55's much larger 378/432-cell Snark probes. This cuts the
Level 2 maximum input population from 432 to 136 while preserving a rich event
set.

#### Constraint and probability passes

After decoding the 16 run outputs, BW56 does substantially more work than
BW55:

1. It records direct local facts from recognized events.
2. It propagates those facts through deterministic Level 2 collision/context
   rules.
3. A feasibility dynamic program checks whether each bit can be zero, one, or
   both under the observations.
4. Bits with only one feasible value are placed in `known_mask`.
5. A second weighted dynamic program calculates relative evidence for the
   remaining bit values.
6. High-confidence unresolved bits are placed in `guess_mask`; ambiguous bits
   remain unmasked.

The generated functions `w2c_bw56_f11()` and `w2c_bw56_f12()` implement the two
large dynamic programs. They enumerate compact three-state transitions and use
likelihood factors visible in the binary, including `0.5`, `0.25`, `0.04`, and
`0.02`.

### 4.4 Recovered guess-selection thresholds

Near the final submission, the decompiled main routine compares the weighted
mass for `bit=1` with the total mass. For a remaining unknown bit, let the
model's inferred probability be:

```text
p1 = weighted mass(bit = 1) / total weighted mass
```

The recovered policy is approximately:

```text
if p1 >= 0.579:
    guess 1
elif p1 <= per_bit_zero_threshold:
    guess 0
else:
    abstain
```

The zero threshold is usually `0.403` and ranges from `0.35` to `0.42` for
special bit positions. The binary also applies the final filter:

```text
guess_mask &= 0xffffffffdffffeff | value
```

This means positions 8 and 29 cannot survive as guessed zero; they may survive
only when the submitted value at that position is one.

These are model thresholds, not scorer thresholds. If `p1` were perfectly
calibrated, the scoring rule would require:

```text
guess 1 only when p1 > 0.60
guess 0 only when p1 < 0.40
```

BW56's more aggressive constants can still be rational if its model is
systematically conservative or the thresholds were empirically calibrated.
They must be judged using held-out full-secret results, not the internal
probabilities alone.

## 5. Exact all-zero-seed breakdown

The full stored evaluation uses challenge seed:

```text
00000000000000000000000000000000
```

### Level submissions

| Level | Secret | Submitted value | Known mask | Guess mask |
|---|---|---|---|---|
| 0 | `0x66505af533720487` | same | `0xffffffffffffffff` | `0x0000000000000000` |
| 1 | `0x0f15849e3b11b5d0` | same | `0xffffffffffffffff` | `0x0000000000000000` |
| 2 | `0xa44617d091f35d67` | `0x000000d001f05d67` | `0x000000f007fc79ff` | `0x70e0e10ac0028600` |

The Level 2 masks are disjoint. They cover:

```text
K = 26 known bits, all correct
G = 18 guessed bits
C = 13 correct guesses
W =  5 wrong guesses
20 bits are unmasked and ignored
```

### Answer weight

```text
known_weight = 26/64
             = 0.40625

guess_weight = (0.2*13 - 0.3*5)/64
             = 1.1/64
             = 0.0171875

answer_weight = 0.4234375
```

### Level 2 score

```text
known contribution        = 923,254.408339 * 26/64
                          = 375,072.103388

13 correct guess credit   = 923,254.408339 * (0.2*13)/64
                          =  37,507.210339

5 wrong guess penalty     = 923,254.408339 * (-0.3*5)/64
                          = -21,638.775195

Level 2 score             = 390,940.538531
```

### Total

```text
Level 0 = 1,071,095.783653
Level 1 = 1,024,427.365027
Level 2 =   390,940.538531
          ----------------
Total   = 2,486,463.687211
```

The call profile is 33 `run` calls, 3 `submit` calls, 1 `finalize` call, and no
`get_rand` calls.

## 6. Second full reference seed

The stored evaluation for seed
`30a085c919e7193d5e97b1acbedd4ea1` provides a useful variability check:

```text
Level 2 known:          K = 19, all correct
Level 2 guesses:        17
Correct guesses:        C = 11
Wrong guesses:          W = 6
Known weight:           0.296875
Guess weight:           0.00625
Level 2 score:          279,861.492528
Total score:          2,375,384.641208
```

Across these two full traces, BW56 made 35 guesses, of which 24 were correct
and 11 wrong: 68.6% accuracy and positive aggregate guess value. Two seeds are
far too few to establish expected score or tail risk, particularly because the
known-mask rule makes rare false proofs expensive.

## 7. Difference from BW55

| Metric | BW55 | BW56 | Change |
|---|---:|---:|---:|
| Level 0 max cells / generations | 55 / 1,000 | 55 / 1,000 | unchanged |
| Level 1 max cells / generations | 155 / 2,442 | 153 / 2,410 | -2 / -32 |
| Level 1 performance | 924,365.148074 | 924,427.365027 | +62.216953 |
| Level 2 max cells / generations | 432 / 4,004 | 136 / 4,056 | -296 / +52 |
| Level 2 performance | 920,932.489451 | 923,254.408339 | +2,321.918887 |

On the all-zero seed:

| Level 2 result | BW55 | BW56 |
|---|---:|---:|
| Known bits | 22 | 26 |
| Guessed bits | 0 | 18 |
| Correct / wrong guesses | 0 / 0 | 13 / 5 |
| Answer weight | 0.343750 | 0.423438 |
| Level 2 score | 316,570.543249 | 390,940.538531 |
| Total score | 2,412,031.474976 | 2,486,463.687211 |

The total improvement is **74,432.212235** points:

- `+62.216953` from Level 1 efficiency;
- `+74,369.995282` from Level 2's combined efficiency, extra proofs, and
  guesses.

On the second stored seed, BW56 improves by **20,911.446822** points. The gain
is positive in both available full traces, but its size is strongly
seed-dependent.

## 8. Improvement priorities

### Priority 1: validate and recalibrate guesses

At BW56's current Level 2 performance:

```text
one correct guessed bit = +2,885.170026
one wrong guessed bit   = -4,327.755039
one random 50/50 guess  =   -721.292507 expected
```

The theoretically optimal action for a calibrated probability is to guess only
above 60% confidence. BW56's inferred 57.9% one-threshold and some zero
thresholds above 40% are aggressive.

The first improvement experiment should therefore require no Life-pattern
change:

1. run a large, fixed training/validation seed corpus;
2. log the model's `p1` for every unknown bit;
3. measure actual correctness by probability bucket and bit position;
4. optimize thresholds for the asymmetric `+0.2/-0.3` payoff;
5. validate on held-out seeds, reporting mean, median, low quantiles, and worst
   known-mask failures;
6. remove or justify the special handling of bits 8 and 29.

If the probability model is well calibrated, moving thresholds to 0.6/0.4
should eliminate negative-expectation guesses. If it is miscalibrated, fit a
calibration map before choosing score-optimal thresholds.

### Priority 2: prove more bits safely

One additional correct known bit is worth:

```text
923,254.408339 / 64 = 14,425.850130 points
```

That is five times the reward for one correct guess and more than three times
the penalty from a wrong guess. Promoting a bit from guess to known is valuable
only when it becomes logically certain:

- correct guess -> correct known: about `+11,540.680104` points;
- wrong guess -> corrected known: about `+18,753.605169` points;
- abstention -> correct known: `+14,425.850130` points.

Never promote a merely high-confidence bit into `known_mask`. A single false
known bit removes all known credit. On the all-zero seed that would erase the
375,072-point known contribution, leaving only the guess component.

Promising directions are additional complementary event signatures, stronger
constraint propagation, explicit use of boundary conditions, and adaptive
later probes chosen from earlier observations.

### Priority 3: characterize the dynamic program

The stripped binary contains tuned likelihoods and per-bit thresholds whose
derivation is not preserved. A successor should reconstruct a readable model
with named states and tests:

- define each observation bit and local transition;
- distinguish hard constraints from soft likelihoods;
- reproduce `f11` feasibility and `f12` weighted mass on captured traces;
- compare modeled probabilities with exhaustive small-context Life results;
- add golden tests for the two stored full seeds;
- generate masks from the readable implementation and compare bit-for-bit with
  the Wasm.

This is likely more productive than editing the generated 489 KB `wasm2c` file
directly.

### Priority 4: use the remaining max-cost headroom

Level 2's current maxima are 136 cells and 4,056 generations.

- Each 120-cell run has 16 cells of density-free headroom.
- Each 128-cell run has 8 cells of density-free headroom.
- The 136-cell runs have no density headroom.
- Every 2,600-generation run can evolve up to 4,056 generations without
  changing the generation score.
- All 16 run slots are occupied, so new probes must be packed into or replace
  existing runs.

Extra probes are score-free inside those maxima, but collision debris and
output ambiguity remain physical constraints.

### Priority 5: treat efficiency as secondary

Approximate performance gain from reducing the current maximum by one:

| Change | Level 0 | Level 1 | Level 2 |
|---|---:|---:|---:|
| One fewer maximum input cell | 45.4416 | 13.3431 | 15.0049 |
| One fewer maximum generation | 2.7130 | 1.1260 | 0.6691 |

Level 2's gain is then multiplied by answer weight. At the zero-seed weight of
0.4234375, removing one maximum cell is worth only about 6.35 final points.

Reducing 16 runs to 15 increases performance by 367.6471 before answer
weighting—about 155.7 final points at that same weight. Losing one known bit
would cost roughly 14,426 points, so evidence coverage dominates efficiency.

Preserve Level 0 and Level 1 exactness. Losing one Level 1 known bit costs the
100,000 exact bonus plus about 14,444 points of performance weight.

## 9. Validation checklist

For every successor artifact:

1. Verify the exact Wasm hash being evaluated.
2. Record all runs' live-cell and generation counts; calculate `Nmax` and
   `Gmax`, not totals.
3. Record `value`, `known_mask`, and `guess_mask` per level.
4. Assert that known and guess masks are disjoint.
5. With an offline oracle, assert
   `(value ^ secret) & known_mask == 0` on every test seed.
6. Count `K`, `C`, and `W`, then reconcile the scorer result exactly.
7. Log model confidence for guessed and abstained bits, including bit position.
8. Test detector families over all relevant local contexts and parity states.
9. Test packed patterns over many full secrets for cross-lane interactions and
   edge effects.
10. Use separate training and held-out seed sets for threshold tuning.
11. Report lower-tail and false-known behavior, not only average score.
12. Confirm `finalize()` is called and all hard CPU, memory, fuel, canvas,
    corner-integrity, run-count, and wall-clock constraints still pass.

## 10. Compact score calculator for BW56

Assuming Levels 0 and 1 are exact, the known bits are all correct, `K < 64`,
and the masks are valid:

```python
P2 = 923_254.4083386543
FIXED_L0_L1 = 2_095_523.148679947


def bw56_total(known_bits, correct_guesses, wrong_guesses):
    answer_units = (
        known_bits + 0.2 * correct_guesses - 0.3 * wrong_guesses
    )
    level2 = P2 * answer_units / 64
    return FIXED_L0_L1 + level2
```

If any known bit is wrong, replace `known_bits` with zero in this calculation.
If all 64 bits are correctly known, add the 100,000 exact bonus.

## 11. Source and evidence map

- [`golduck/sim.py`](../../golduck/sim.py): authoritative scoring and mask
  semantics.
- [`golduck/level0.py`](../../golduck/level0.py),
  [`golduck/level1.py`](../../golduck/level1.py), and
  [`golduck/level2.py`](../../golduck/level2.py): level geometry and limits.
- [`solution/bw56.json`](../../solution/bw56.json): original package containing
  the base64-encoded artifact.
- [`solution/bw56.wasm`](../../solution/bw56.wasm): decoded executable artifact.
- [`solution/bw56.c`](../../solution/bw56.c) and
  [`solution/bw56.h`](../../solution/bw56.h): exact instruction-level WABT
  translation, with generated identifiers.
- [`solution/BW56_REVERSE_ENGINEERING.md`](../../solution/BW56_REVERSE_ENGINEERING.md):
  artifact provenance and initial strategy summary.
- [`score_history.json`](../../score_history.json): two complete BW56 reference
  evaluations, including run RLEs, masks, bit correctness, and score details.
- [`visualizer_eval.py`](../../visualizer_eval.py): portable Life execution and
  live score instrumentation using the production scoring functions.
- [`runner_core.py`](../../runner_core.py): Wasm ABI and hard execution limits.

