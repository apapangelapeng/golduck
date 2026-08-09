"""Capture the patterns a solution wasm feeds to run(), without simulating.

Executes the wasm with stubbed host imports: run() records the submitted
pattern and returns an empty viewing-window RLE, so no bgolly is needed.
Pattern generation in solutions must not depend on run() output for the
capture to be faithful; submissions are recorded but their values will
reflect the stubbed (empty) outputs.
"""

from __future__ import annotations

from pathlib import Path

from golduck.levels import LEVELS

_FUEL = 50_000_000_000


def capture_runs(wasm_path: Path, output_for=None) -> dict[str, object]:
    """Run the wasm with stubbed hosts; record runs and submissions.

    output_for(level, rle, generations) may return the RLE bytes run()
    should hand back; returning None falls back to the empty-window stub.
    """
    import wasmtime

    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    store = wasmtime.Store(engine)
    store.set_fuel(_FUEL)
    module = wasmtime.Module(engine, wasm_path.read_bytes())
    linker = wasmtime.Linker(engine)

    runs: list[dict[str, object]] = []
    submissions: list[dict[str, object]] = []
    state: dict[str, object] = {}

    def stub_output(level: int) -> bytes:
        if level in LEVELS:
            _, _, w, h = LEVELS[level](0).get_viewing_rect()
        else:
            w = h = 0
        return f"x = {w}, y = {h}\n!\n".encode("ascii")

    def imp_get_rand(caller, salt):
        return 0

    def imp_run(caller, level, pattern_ptr, pattern_len, generations, out_ptr):
        memory = state["memory"]
        rle = memory.read(store, pattern_ptr, pattern_ptr + pattern_len)
        rle_text = rle.decode("ascii", errors="replace")
        runs.append(
            {
                "level": int(level),
                "generations": int(generations),
                "rle": rle_text,
            }
        )
        payload = None
        if output_for is not None:
            payload = output_for(int(level), rle_text, int(generations))
        if payload is None:
            payload = stub_output(int(level))
        memory.write(store, payload, out_ptr)
        return len(payload)

    def imp_submit(caller, level, value, known_mask, guess_mask):
        submissions.append(
            {
                "level": int(level),
                "value": f"0x{value & ((1 << 64) - 1):016x}",
                "known_mask": f"0x{known_mask & ((1 << 64) - 1):016x}",
                "guess_mask": f"0x{guess_mask & ((1 << 64) - 1):016x}",
            }
        )
        return 0

    def imp_finalize(caller):
        return 0

    i32 = wasmtime.ValType.i32
    i64 = wasmtime.ValType.i64
    defs = [
        ("get_rand", [i32()], [i64()], imp_get_rand),
        ("run", [i32()] * 5, [i32()], imp_run),
        ("submit", [i32(), i64(), i64(), i64()], [i32()], imp_submit),
        ("finalize", [], [i32()], imp_finalize),
    ]
    for name, params, results, func in defs:
        linker.define(
            store,
            "env",
            name,
            wasmtime.Func(
                store, wasmtime.FuncType(params, results), func, access_caller=True
            ),
        )

    instance = linker.instantiate(store, module)
    exports = instance.exports(store)
    memory = exports.get("memory")
    if memory is None:
        raise RuntimeError("wasm module does not export memory")
    state["memory"] = memory
    run_entry = exports.get("run_entry")
    if run_entry is None:
        raise RuntimeError("wasm module does not export run_entry")
    run_entry(store)

    return {"runs": runs, "submissions": submissions}
