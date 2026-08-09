# golduck runner API

## Wasm ABI

Required exports: `memory` (memory), `scratch_ptr`, `scratch_cap` (i32 globals), functions `run_entry()` (entry).

Imports from `"env"`:

- `get_rand(i32) -> i64`
- `run(i32, i32, i32, i32, i32) -> i32`
- `submit(i32, i64, i64, i64) -> i32`
- `finalize() -> i32`

## Local visualizer evaluation

### Agent simulation tool

The Python function `agent_simulate_and_score(payload)` in
`agent_simulation.py` exposes the same simulation and scoring implementation to
AI agents without changing the Wasm ABI. It is also available as
`POST /api/agent/simulate`. Start a stateful exploration session:

```json
{"action":"start","seed":"0123456789abcdef0123456789abcdef"}
```

Retain the returned `session_id`, then make up to the level's normal run limit:

```json
{"action":"run","session_id":"...","level":1,"pattern":"x = 0, y = 0\n!","generations":0}
```

Each run returns the normal viewing-area RLE as `output_rle`, plus a numeric
`score`, `score_type`, and complete `score_breakdown`. Before submission the
score type is `performance`; after a `submit` action it is the actual
answer-weighted `submitted` score. The same session retains all run counts and
observations, enabling dependent multi-run bit exploration. Supported actions
are `start`, `run`, `submit`, `status`, `finalize`, and `close`. Submission
values and masks may be JSON integers or `0x`-prefixed integer strings. Hidden
secret values are never included in agent responses. The agent path calls the
production scorer directly, stores only constant-size metadata per run, and
passes in-process Life patterns to scoring without a full-canvas RLE
encode/parse round trip.

`GET /api/evaluate/<solution.wasm>?seed=<32 hex digits>` executes the selected
Wasm against the seed-derived secret for every implemented level. The response
is newline-delimited JSON (`application/x-ndjson`) and emits `start`,
`parallel_batch_started`, `parallel_progress`, `run_started`, `run_complete`,
`submission`, and `complete` events. Independent Life runs execute concurrently;
adaptive runs whose inputs depend on earlier outputs are discovered and executed
in subsequent parallel batches. Failures emit an `error` event. Run and
submission events include the current per-level score breakdown so multi-run
solutions can be displayed incrementally.

The Scoring tab expands those snapshots into the exact arithmetic for every
level submission: run, density, and generation bonuses; known and guessed bit
weights; weighted score; exact-answer bonus; and the final level total. It also
lists the completed run inputs used by the logarithmic ratios. The view updates
during a live evaluation and works with records restored from
`score_history.json`.

Evaluations are keyed by solution filename, Wasm SHA-256, and seed. Their seed,
run data, and full score breakdown are written atomically to
`score_history.json`; a matching completed evaluation is restored after a page
refresh without rerunning the Wasm. `GET /api/solution/<solution.wasm>` accepts
optional `seed` and `level` query parameters so the comparison table can restore
an exact single-level result and its runs directly in the Visualizer.

The Secret preview tab generates a random 128-bit seed in the browser, calls
`GET /api/level/<level>?seed=<32 hex digits>`, and fits only the returned secret
cells to its canvas. It uses the same seed derivation and level implementation
as scoring.

The Idea Lab tab is a client-side Life workbench built on `GET /api/levels` and
the same seed-derived level endpoint. It always includes the complete secret,
restricts stamp placement to the contestant rectangle, and saves or loads
versioned `golduck-idea-lab` JSON containing stamps, custom RLE, view settings,
the exact evolved state, and any loaded solution scoring context. It can import
any individual run from the latest
completed evaluation of a Wasm solution, switching to that evaluation's seed
and generation target. At the target generation it displays the production
run/density/generation formula as a projected exact score; this assumes the
all-known 64-bit answer is correct and includes the 100,000 exact-answer bonus.
Bulk fleet mode repeats the selected, transformed stamp in a configurable
row/column array with independent horizontal and vertical gaps; the array is
placed, removed, and undone as one object and is included in saved JSON state.

`GET /api/comparison` returns the current Wasm files as rows, the union of their
completed saved seeds as columns, each total's per-level score breakdown, and
the winning solution name or names for each seed. Each solution row also contains
its win count, total run and generation counts, and per-level run count and
maximum generation count used by scoring. These workload values are shown once
per solution because they are fixed by the solution. Submitted values and answer
masks are omitted. Ties count as one win for every tied solution. Saved records
whose Wasm hash no longer matches the current file are excluded. The comparison UI can
rank either total scores or one selected level. It can generate ten shared random
seeds at once and evaluate them for every included
solution; removed rows remain excluded from later batches. All ten seed pipelines run
concurrently in a server-owned background job. Use
`POST /api/comparison/batch` to start or reconnect to the active batch and
`GET /api/comparison/batch` to read its progress. Because the server owns the
job, scoring and atomic JSON persistence continue after the browser navigates
away, refreshes, or disconnects. Include a `solutions` array of Wasm filenames
to restrict either batch mode. Include `"level":0`, `1`, or `2` to score only
that level; calls to other levels receive inert outputs and do not run Life
generations. Partial level results are merged into the existing saved cell. A
missing or `null` level retains full-total scoring. For example,
`{"mode":"fill_empty","solutions":["bw57.wasm"],"level":2}` creates a sparse
background batch containing only that row's currently empty Level 2 seed cells. Omitting
`solutions` retains the all-solutions API behavior; the default mode remains
`random_seeds`.

### GitHub Wasm webhook

`POST /api/webhooks/github/wasm` installs new or updated `.wasm` files from one
configured GitHub repository. Configure the visualizer process with:

- `GOLDUCK_GITHUB_REPOSITORY=owner/repository`
- `GOLDUCK_GITHUB_WEBHOOK_SECRET=<random shared secret>`
- `GOLDUCK_GITHUB_TOKEN=<GitHub token>` only when the repository is private

In the repository's **Settings → Webhooks**, use the public HTTPS URL
`https://<visualizer-host>/api/webhooks/github/wasm`, JSON content type, the
same secret, and enable Push and Release events. A visualizer bound only to
`127.0.0.1` needs an HTTPS tunnel or deployment before GitHub can deliver to
it.

Push deliveries ingest added or modified `.wasm` paths at the delivered commit;
Release deliveries ingest published, released, or edited `.wasm` assets. The
endpoint verifies GitHub's SHA-256 signature and exact repository name, limits
downloads to the runner's Wasm size cap, validates WebAssembly and ABI imports,
traces an active Level 3 or Level 4 run, and atomically replaces the matching
file in `solution/`. Valid files then appear through `GET /api/solutions` and
the visualizer's solution picker.
