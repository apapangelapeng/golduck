# Level 2 multi-row return exploration

## Result

Multi-row spaceship waves were tested with the production
`agent_simulate_and_score(payload)` interface.  Dense vertical waves usually
destroyed the useful return instead of adding information.  The search did,
however, expose a second return from the existing compact reflector.  Reading
both returns at generation 4,035 produced `solution/explore11.wasm`.

Compared with `explore8.wasm` on the same 13 saved seeds:

| Artifact | Mean score | Mean Level 2 known bits | Level 2 max generation |
|---|---:|---:|---:|
| `explore8.wasm` | 2,362,568.680507 | 18.153846 | 4,017 |
| `explore11.wasm` | **2,380,320.333893** | **19.384615** | 4,035 |
| Difference | **+17,751.653386** | **+1.230769** | +18 |

Two of the 13 seeds contain the new return.  Each gains eight correct known
bits and about 115,407 total points.  The other eleven seeds lose only
1.73--5.50 points from the 18 additional generations.  Every known mask was
correct.

The final Wasm SHA-256 is:

```text
6f0640a5293d6a6f57806ec5237aeda2163ff0fe68323215bc661e06a5d3e4c7
```

## What happened to the extra rows

The first search used two or three compact northbound LWSS rows aimed at the
same secret location.

- A trailing ship normally annihilated the reflected LWSS.
- Interleaving two dense residue rows suppressed every canonical return in
  the tested cases.
- A 24-cell vertical gap occasionally generated a new mirrored southbound
  LWSS, but it occurred only once in a 1,024-seed isolated sample and depended
  on a wider secret context.  Packing nearby lanes suppressed it further.
- Repeating the diagonal glider in a compact-reflector lane preserved the
  first return at spacings of eight cells or more, but did not produce a
  reliable second marker in the 32-seed spacing scan.

Those geometries were not competitive enough to put in the submitted
artifact.  The reproducible search code is in
`analysis/level2/multirow_fleet_search.py`.

## The useful second return

Sampling the original compact-reflector fleet later revealed another glider
trajectory.  At generation 4,035 the two useful families are disjoint:

- the original event still identifies five-bit context `0x07` with parity 0;
- the new event identifies eight-bit context `0x3e` with parity 1.

The new glider is decoded only when its complete five-cell phase is present.
A hit directly sets the corresponding eight-bit value and known mask.  Misses
are ignored, because neighboring packed lanes can suppress this return.

The agent validation included:

- 64 random seeds, 512 packed-fleet runs, and 4,096 lane observations;
- 59/59 correctly classified original returns;
- 8/8 new-family returns matching `context=0x3e, parity=1`;
- a targeted bit-32 check with 37 true positives, 27 suppressed returns,
  zero false positives, and 256 true negatives; and
- exact scoring of the compiled artifact on all 13 shared comparison seeds.

`analysis/level2/reflector_return_search.py` repeats the packed return-family
scan through the agent interface.

## Build

```sh
/opt/homebrew/opt/llvm/bin/clang --target=wasm32 -O3 -nostdlib \
  -DEXPLORE_CSP -DEXPLORE_WEIGHTED \
  -DEXPLORE_COMPACT_LWSS -DEXPLORE_COMPACT_REFLECTOR \
  -DEXPLORE_COMPACT_SNARK -DEXPLORE_EARLY_REFLECTOR \
  -DEXPLORE_EARLIEST_REFLECTOR -DEXPLORE_END_BOUNDARY \
  -DEXPLORE_LATE_RETURNS \
  -Wl,--no-entry -Wl,--export-memory -Wl,--allow-undefined \
  -Wl,--strip-all -Wl,--export-dynamic -I solution \
  solution/bw55.c -o solution/explore11.wasm
```

The artifact passes `wasm-validate`.  The Python and JavaScript project test
suites also pass.
