# Spacefiller bitmap exploration

This is a new Level 2 exploration; it does not replace or modify `bw55.wasm`.

## Artifacts

- `solution/spacefiller_bitmap_exploration.c`
- `solution/spacefiller_bitmap_exploration.wasm`
- `solution/spacefiller2_shape.h`
- `solution/spacefiller2_bitmap_table.h`

## Decoder

Each run launches a 187-cell Spacefiller-2 pattern at an overlapping window of
the 64-symbol secret. The decoder reads a 100-by-10 return bitmap and compares
it with 247 exact prototype outcomes. It combines the observations with the
legal four-symbol transition graph, using dynamic programming and suffix lower
bounds to reconstruct whole-secret candidates rather than guessing individual
unknown bits.

Every surviving candidate is recreated exactly. The implementation also
reproduces CPython's integer-seeded MT19937 behavior and checks the secret-driven
left/right bridge choices from `random.Random(secret).randrange(2)`. If the
first 15 overlapping probes leave more than one candidate, an adaptive 16th
probe is selected to distinguish them.

The detector allows a total Hamming correction cost of eight cells. That bound
was chosen from direct production-path `agent_simulate_and_score` measurements;
the worst observed disturbance was eight cells in one probe. It is an empirical
noise bound, not a proof over all possible secrets.

## Validation

- WebAssembly validation: passed.
- Repository Python tests: 50/50 passed.
- JavaScript tests: 18/18 passed.
- Full Level 2 evaluation: 64/64 exact bits on all 13 comparison seeds.
- Those evaluations used 15 runs and scored `3121133.696957` each.
- Additional worst-observed-noise seed: 64/64 exact bits in 15 runs.

SHA-256 of the exploration Wasm:

`a4b9ed6adf5d33784db2765bd7c7e573873043bba76179497e6924eccd4dc7c4`

## Build

```sh
/opt/homebrew/opt/llvm/bin/clang --target=wasm32 -O3 -nostdlib \
  -DEXPLORE_SPACEFILLER2 \
  -Wl,--no-entry -Wl,--export-memory -Wl,--allow-undefined \
  -Wl,--strip-all -Wl,--export-dynamic -I solution \
  solution/spacefiller_bitmap_exploration.c \
  -o solution/spacefiller_bitmap_exploration.wasm
```
