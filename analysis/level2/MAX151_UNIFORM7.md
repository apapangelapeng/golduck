# Max151 uniform-seven optimization

## Result

`solution/max151_uniform7.wasm` improves the Level 2 decoder derived from
`max151_adaptive8.wasm`.  It uses seven overlapping Max151 observations first,
constructs the exact set of 64-bit CPython-MT candidates consistent with the
transcript, and only runs an additional probe while that set is ambiguous.

Exact production-engine evaluation on the 30 saved seeds produced:

| Artifact | Exact | Mean runs | Run range | Run distribution |
|---|---:|---:|---:|---:|
| `max151_adaptive7.wasm` | 29/30 | 8.800 | 7-11 | 7: 1, 8: 9, 9: 17, 10: 1, 11: 2 |
| `max151_uniform7.wasm` | **30/30** | **7.833** | **7-9** | **7: 13, 8: 9, 9: 8** |

For seed `52ddf222fdd1519b665ac4d91fe0e843`, the new artifact returns all
64 bits exactly in seven runs.  Its Level 2 score is `1,032,692.557501`, and
the complete three-level evaluation succeeds with score `3,128,206.714666`.

## Decoder

The initial context starts are `0, 9, 18, 27, 35, 45, 55`.  Their 12-bit
physical windows overlap and cover the whole 64-bit secret.  For each of the
seven observed classes, the decoder enumerates the distinct legal local
binary masks from the existing 47,321-context calibration table.  A depth-first
overlap join produces whole-secret candidates, and each leaf is checked with
the exact CPython-MT parity model.

If the join is not unique, the decoder evaluates every unused physical start
from -3 through 61 and chooses the one whose largest candidate bucket is
smallest.  The observed class filters the survivor set; this repeats only
while necessary.  A full answer is submitted only for one exact survivor.
When ambiguity or a defensive capacity bound remains, the decoder submits
only bits common to every survivor or the conservative forced literals.

Start 35 deliberately replaces 36.  Exact-Life calibration found one saved
secret whose physical return at start 36 did not match the local table;
start 35 avoids that boundary case without reducing coverage.

## Higher-cell shape search

Allowing more initial cells did not yield a reliable one-run or sub-seven
shape in the tested families:

- Max151 pairs and vertically staggered grids were scanned across 750 coarse
  placements.  No placement preserved two secret-dependent return channels.
- Time-separated Max151 copies were not independent: the first interaction
  changed the secret strip and suppressed the later return.
- Direct unions of compact reflector residue banks damaged one another.
  Catalyst-only displacement preserved each isolated reflection, but the
  simultaneously packed banks still suppressed the useful events; an
  all-pairs audit across 128 seeds found no compatible pair.
- Opposing 904-cell Halfmax fronts annihilated into output independent of the
  secret.  Eight orientations of the 958-cell Quartermax were also tested;
  none both interacted with the secret and returned a usable observation.
- Waiting longer with one Max151 increased spatial reach, but not the number
  of independent observation classes.

This is an empirical result for those shape families, not a proof that every
larger Life pattern needs seven runs.  There is, however, a hard bound for the
current Max151 channel: one run has at most 1,034 calibrated classes, so a
decision tree of depth six has fewer than `1034^6 < 2^64` leaves.  A six-run
exact decoder therefore needs a genuinely richer physical observation, not
only more copies of this front.

The experiments are reproducible with:

- `analysis/level2/search_max151_time_multiplex.py`
- `analysis/level2/search_compact_reflector_multiplex.py`
- `analysis/level2/search_halfmax_multiplex.py`
- `analysis/level2/search_max151_generation.py`
- `analysis/level2/count_uniform7_candidates.c`

## Build and validation

```sh
./build_solution.sh --no-visualize solution/max151_uniform7.c
wasm-validate solution/max151_uniform7.wasm
python run_cli.py solution/max151_uniform7.wasm \
  --seed-hex 52ddf222fdd1519b665ac4d91fe0e843
```

Final artifact size: 42,565 bytes.

SHA-256:

```text
315c4b9a131bba3d5970d2fc143cd5bd8edaaa9737560466228700737b15aa9d
```

All 57 Python unit tests pass.  Rebuilding the original adaptive-eight and
adaptive-seven targets remains byte-identical to their prior artifacts.
