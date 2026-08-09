# Level 3 shape and configuration search

## Selected optimization

The retained solver is the existing two-edge fleet: each early run sends one
orthogonal spaceship toward each outer glyph.  It is random-secret safe and
does not depend on a table of saved seeds.

The optimized artifact advances each periodic ship to the latest equivalent
phase that still fits in the contestant rectangle:

| Probe | Original | Optimized | Saved |
| --- | --- | --- | ---: |
| First seven paired c/2 ships | row 90, generation 3100 | row 0, generation 2920 | 180 |
| Northwest glider | `(1662, 2)`, generation 4400 | `(1660, 0)`, generation 4392 | 8 |
| Two northeast gliders | `(124, 50)` / `(130, 50)`, generation 4500 | `(174, 0)` / `(180, 0)`, generation 4300 | 200 |

These are exact Game of Life phase translations. They preserve the future
viewing RLE, the number of live input cells, and every decoder decision. The
generation-5020 paired gliders already touch row 0. The generation-9000
Copperhead also touches row 0, and advancing it by one generation increases
its population from 28 to 30, so neither change was retained.

## Other configurations tested

| Configuration | Result |
| --- | --- |
| Four c/2 lanes aimed at digits 0, 5, 10, and 15 | Returning debris crossed lanes at 50-cell spacing and produced false negatives, including at the outer lane. |
| Standard c/2 ships at interior offsets, in both orientations | Signatures that looked unique for an isolated glyph stopped being invariant under random neighboring glyphs. |
| Mirrored edge and interior launches | A few isolated signatures survived, but not enough for a sound general decoder. |
| 28-cell c/10 Copperhead | Useful edge behavior, but late output was strongly dependent on surrounding glyphs. |
| 151-cell Max pattern | Broader response, but each observation depended on scattered digits rather than a clean local nibble. |

The search therefore favored the well-separated two-edge configuration over
adding more simultaneous lanes or a large non-local pattern.

## Verification

- 11 stored seeds: exact viewing outputs and submissions matched on all 11;
  9 scores improved. Mean Level 3 gain was 7.669515 and the maximum was
  20.289006 points.
- 40 fresh held-out seeds: exact viewing outputs and submissions matched on
  all 40; 25 scores improved and 15 were unchanged. Mean Level 3 gain was
  4.274298 and the maximum was 20.289006 points.
- The unchanged cases reached a later generation-5020 or generation-9000
  fallback, which still determines the generation bonus.

Rebuild with:

```sh
.venv/bin/python analysis/level3/build_close_launch.py
```

The source artifact SHA-256 is
`d7d048fa6390356bba1c6f2e46d32b7cad6d1018b1fde451a67dbe2d32458918`.
The optimized artifact SHA-256 is
`9c6a345865a085c9b6117de5e10859ff0e8339f07b4cac5fe15390d55ba2ae42`.
