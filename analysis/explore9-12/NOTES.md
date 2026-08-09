# explore9–explore12: probe bit-location and detector experiments

These four artifacts were built from `solution/bw55.c` (explore9/10 via
compile flags plus a snark-bound edit; explore11/12 add a new detector
family).  All level-2 physics claims below were verified with the
visualizer's exact B3/S23 engine (`visualizer_eval.evolve_life`); the
validation scripts live in the session scratchpad and their key results are
summarized here.

## Verified detector predicates

Exhaustive sweeps over every 7-bit local context (bits c-3..c+3) and every
parity assignment of the runs-of-ones confirmed the firing rules, both
directions (fires if and only if):

| detector | context requirement (bit positions) | parity gate | asserts |
|---|---|---|---|
| recipe 0 | c-1,c,c+1,c+2 = 1,1,0,1 | parity(run∋c) = 1 | `0x1e/0x16 << (c-2)` |
| recipe 1 | c-1,c,c+1,c+2 = 1,0,1,1 | parity(run∋c+1) = 0 | `0x1e/0x1a << (c-2)` |
| recipe 2 | c-2..c+1 = 1,0,1,1 | parity(run∋c) = 0 | `0x0f/0x0d << (c-2)` |
| recipe 3 | c-2..c+1 = 1,1,0,1 | parity(run∋c-1) = 1 | `0x0f/0x0b << (c-2)` |
| snark | c-2..c+2 = 1,1,1,0,0 | parity(run∋c) = 0 | `0x1f/0x07 << (c-2)` |
| g100 (new) | c-3..c = 0,1,0,0 | parity(run∋c-2) = 0 | `0x0f/0x02 << (c-3)` |

Key structural fact: **recipes 2/3 are shifted duplicates of recipes 1/0**
(`r2@c ≡ r1@(c-1)`, `r3@c ≡ r0@(c-1)`) — identical facts, identical parity
conditions.  Activating them adds no information except at the strip edges.
That is why `EXPLORE_ALL_LWSS` scores *worse* than stock bw55: it pays for
redundant coverage with the snark family.

The view-outcome space of the stock probes is **binary**: for every
non-firing (context, parity) case, nothing reaches the viewing rect at the
decode generation, so richer signature decoding of the existing probes
cannot extract more bits.

## explore9 — direct facts, edge coverage (mean 2,353,366)

`-DEXPLORE_COMPACT_LWSS` (residue-3 LWSS runs start at center 1, covering
bits 0–3 events; the 8-cell compact probe is generation-1-identical to the
LWSS, verified) plus a source edit extending snark residues 6/7 to centers
62/63 (bits 60–63 events; the s=6 run grows to 8 lanes = 432 cells = Nmax,
so performance cost is zero).

Packed-run validation: center-1 runs 304/304 clean; snark 62/63 extension
loses an interior lane event in ~1.3% of runs (always the safe direction —
missing, never wrong).  Monte Carlo: meanK 17.43 → 17.90.

## explore10 — CSP + weighted guessing (mean 2,355,359)

`-DEXPLORE_COMPACT_LWSS -DEXPLORE_CSP -DEXPLORE_WEIGHTED`, stock snark
bounds.  The forward/backward DP in bw55.c was ported to Python and checked
against 3,000 random secrets driven by the verified predicates: **zero
unsound known bits**; guesses run 5.6/secret at 68.6% accuracy (break-even
is 60%).  The snark 62/63 extension is deliberately NOT combined with the
DP: the DP treats missing events as evidence, so the extension's rare
interference losses could force wrong known bits.  End-to-end, the wasm
submissions match the Python model bit-for-bit.

## explore11/12 — the g100 (isolated-one) detector (means 2,281,406 / 2,287,383)

A sweep of the probe aim space (LWSS/MWSS × phase × x-offset × timing
parity, 5,346 sims) found several new event classes.  The strongest: an
upward LWSS at offset −4, ly 195 fires on an **isolated one** (bits
c-3..c = 0,1,0,0, parity 0) and ejects a SE glider.  The stock snark
reflector translated by (+25,+40) catches it — the incoming glider phase
matches the stock catch exactly — and returns a marker glider to view
(3c+395, 9) at generation 3880.  Predicate verified over all 577
context/parity cases; decode uses a 5×5 guard ring so debris can never fake
a marker.

**Result: negative.**  Non-firing contexts of the same probe eject junk
gliders that drift hundreds of columns across the run; over the ~5,500
generations of exposure they destroy most real g100 events (losses are
always the safe direction — the guard ring produced zero false positives in
480 packed-run sims and zero wrong known bits on the board).  A clipped
local-window validation showed only ~6% loss; production-canvas runs lose
the large majority.  Realized meanK ≈ 12.9 (≈ LWSS-only), so both variants
score *below* bw55.  explore12 wires g100 into the weighted DP as
**one-sided evidence** (observed events constrain, absences never prune),
verified sound at an injected 80% loss rate over 1,200 secrets.

Lesson for successors: any new detector family must be validated for junk
emission on ALL non-firing contexts at full canvas width, not just for its
firing signature.  The g100 fact class is real and worth ~+3 mean known
bits if a junk-free probe aim (or a junk-tolerant run layout, e.g. wider
lane pitch with more runs, or an NE-glider-style probe with a quiet
non-firing profile) can be found.

## Score comparison (13 shared seeds)

| solution | mean | notes |
|---|---:|---|
| explore8 | 2,362,569 | compact reflector + weighted (parallel session) |
| explore7 | 2,356,021 | |
| explore10 | 2,355,359 | this session |
| explore9 | 2,353,366 | this session |
| bw55 | 2,343,404 | baseline |
| explore12 | 2,287,383 | this session (g100, one-sided DP) |
| explore11 | 2,281,406 | this session (g100, direct facts) |
