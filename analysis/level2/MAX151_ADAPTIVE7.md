# Max151 adaptive-seven optimization

## Result

`solution/max151_adaptive7.wasm` is a correctness-preserving extension of
`max151_adaptive8.wasm` that can finish Level 2 in seven runs.  It only exits
early when the exact CPython-MT candidate sieve proves all 64 bits.

The original first eight starts are
`0, 6, 18, 30, 36, 42, 48, 54`.  The new build swaps the last two, so its
first seven starts are `0, 6, 18, 30, 36, 42, 54`.  If those are ambiguous,
start 48 restores exactly the original eight-observation transcript.  The
remaining starts `12, 58, 24` are unchanged.

On seed `52ddf222fdd1519b665ac4d91fe0e843`, exact Life evaluation gives:

| Artifact | Level 2 runs | Known bits | Exact | Level 2 score |
|---|---:|---:|:---:|---:|
| `max151_adaptive8.wasm` | 8 | 64 | yes | 1,031,303.668612 |
| `max151_adaptive7.wasm` | **7** | **64** | **yes** | **1,032,692.557501** |

## Decoder change

The old decoder retained only the central safe literals from each of the
1,034 observation classes.  The exhaustive 47,321-context table contains
more information: a class forces between 5 and 12 binary positions, with a
mean of 8.529.  The new build derives those masks from the existing table at
startup and merges all in-range literals before running the exact MT sieve.

Early sieving is capped at 16 unknown bits (65,536 candidates).  Ambiguous or
larger cubes continue to the original run-8 observation set, so an unproven
full answer is never submitted.

The Max151 RLE is emitted from 23 normalized rows at runtime.  This removes
the eleven duplicated full-width RLE strings from the optimized Wasm and
reduces its size from 39,567 to 35,197 bytes.

## Shape search

Exact-Life experiments did not find a credible one-run or sub-seven Max
variant:

- A later single Max sees more of the strip, but a separated later live run is
  overwritten after the front meets the first live run.  Waiting longer adds
  reach, not independent information.
- Same-phase pairs at useful horizontal separations annihilate before a
  return reaches the view.
- A scan of 588 horizontally separated, vertically phased Max pairs found 11
  tiny surviving returns.  All 11 were constant collision debris: none
  changed for singleton secret bits 0 through 30.
- The mirror-union and mirror-XOR fused fronts also died before returning.

The current observation channel carries about 9.2 bits per run on legal local
contexts, putting seven runs very close to its information limit.  Getting to
six, especially one, needs a genuinely different front that preserves
multiple spatially independent collision scars; translating, delaying, or
fusing this Max151 predecessor did not do that.

The reproducible pair search is
`analysis/level2/search_max151_multiplex.py`; the native exact-candidate audit
is `analysis/level2/count_max151_candidates.c`.

## Build and validation

```sh
./build_solution.sh --no-visualize solution/max151_adaptive7.c
wasm-validate solution/max151_adaptive7.wasm
```

Final SHA-256:

```text
19162610451b4103f75b3c61df4ac949c34a52380ecdcfa853ca788e4365e871
```

The non-optimized compile of `max151_adaptive8.c` still reproduces its
original SHA-256 exactly.  All 57 Python unit tests pass.
