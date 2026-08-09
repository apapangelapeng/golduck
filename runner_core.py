"""Runner core for challenge: golduck.
Challenge simulation semantics live in the external sim module.
"""

from __future__ import annotations

import base64 as _base64
import struct
import threading
import time
import weakref
from collections.abc import Callable
from typing import Any

from sim import Sim as _SimClass

MAX_SUBMITS = 32
MAX_WASM_BYTES = 1048576
RUN_TIMEOUT_SECONDS = 60
WASM_FIELD = "artifact_b64"


def empty_counts() -> dict[str, int]:
    return {
        "get_rand": 0,
        "run": 0,
        "submit": 0,
        "finalize": 0,
    }


def _is_bool(v: object) -> bool:
    return isinstance(v, bool)


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_string(v: object) -> bool:
    return isinstance(v, str)


def sanitize_error(message: object) -> str:
    text = str(message).replace("\n", " ").replace("\r", " ").strip()
    if len(text) > 500:
        text = text[:500]
    return text


class BadRequest(ValueError):
    pass


def _req_int(value: object, name: str) -> int:
    try:
        return int(value)  # type: ignore
    except (TypeError, ValueError):
        raise BadRequest("field " + name + " must be an integer")


def _req_seed(value: object, name: str, nbytes: int) -> bytes:
    if not isinstance(value, str):
        raise BadRequest("field " + name + " must be a hex string")
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raise BadRequest("field " + name + " is not valid hex")
    if len(raw) != nbytes:
        raise BadRequest("field " + name + " must encode " + str(nbytes) + " bytes")
    return raw


def _req_b64(value: object, name: str, _is_wasm: bool) -> bytes:
    if not isinstance(value, str):
        raise BadRequest("field " + name + " must be a base64 string")
    try:
        return _base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise BadRequest("field " + name + " is not valid base64")


def parse_request(body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise BadRequest("expected JSON object")
    out: dict[str, Any] = {}
    if "artifact_b64" not in body:
        raise BadRequest("missing field " + "artifact_b64")
    out["artifact_b64"] = _req_b64(body.get("artifact_b64"), "artifact_b64", True)
    if len(out["artifact_b64"]) > MAX_WASM_BYTES:
        raise BadRequest("wasm artifact too large")
    if not out["artifact_b64"].startswith(b"\x00asm"):
        raise BadRequest("not a wasm binary")
    if "seed_hex" not in body:
        raise BadRequest("missing field " + "seed_hex")
    out["seed_hex"] = _req_seed(body.get("seed_hex"), "seed_hex", 16)
    if "team_id" not in body:
        raise BadRequest("missing field " + "team_id")
    out["team_id"] = _req_int(body.get("team_id"), "team_id")
    if "enabled_stages" not in body:
        raise BadRequest("missing field " + "enabled_stages")
    out["enabled_stages"] = _req_int(body.get("enabled_stages"), "enabled_stages")
    return out


def build_sim(request: dict[str, Any]) -> _SimClass:
    return _SimClass(
        seed_hex=(request["seed_hex"]),
        team_id=(request["team_id"]),
        enabled_stages=(request["enabled_stages"]),
    )


# Wall-clock budget for one run, tracked per request thread so a batch run
# that builds several guests still shares a single deadline.
_EPOCH_TICK_SECONDS = 0.1
_run_deadline = threading.local()


def begin_run_deadline() -> float:
    at = time.monotonic() + RUN_TIMEOUT_SECONDS
    _run_deadline.at = at
    return at


def adopt_run_deadline(at: float) -> None:
    # For guests built off the request thread (e.g. a batch sim using a
    # thread pool): share the deadline the request started with.
    _run_deadline.at = at


def _remaining_run_seconds() -> float:
    at = getattr(_run_deadline, "at", None)
    if at is None:
        return float(RUN_TIMEOUT_SECONDS)
    return at - time.monotonic()


def _start_epoch_ticker(engine: Any, deadline: float) -> None:
    # Wasmtime only checks the epoch deadline when the epoch advances, so
    # something has to advance it while the guest runs. The ticker holds only
    # a weak reference, so it stops as soon as the host is dropped (normally
    # right after the run) instead of idling until the deadline.
    ref = weakref.ref(engine)

    def tick() -> None:
        while True:
            time.sleep(_EPOCH_TICK_SECONDS)
            live = ref()
            if live is None:
                return
            live.increment_epoch()
            del live
            if time.monotonic() > deadline + _EPOCH_TICK_SECONDS:
                return

    threading.Thread(target=tick, daemon=True).start()


def is_run_timeout(exc: BaseException) -> bool:
    # Epoch traps surface as wasmtime.Trap with trap_code INTERRUPT; fuel
    # exhaustion keeps its own (OUT_OF_FUEL) failure mode.
    return getattr(getattr(exc, "trap_code", None), "name", "") == "INTERRUPT"


RESP_KINDS: dict[str, tuple[int | None, Callable[[Any], bytes]]] = {
}


def pack_envelope(kind_name: str, obj: Any) -> bytes:
    tag, fn = RESP_KINDS[kind_name]
    if tag is None:
        return fn(obj)
    error_code = obj.get("error_code") if isinstance(obj, dict) else None
    if error_code is None:
        return struct.pack("<II", 0, tag) + fn(obj)
    return struct.pack("<II", 1, int(error_code))


class WasmHost:
    def __init__(self, sim: _SimClass, wasm_bytes: bytes) -> None:
        import wasmtime  # type: ignore

        self.wasmtime = wasmtime
        self.sim = sim
        self.call_counts = empty_counts()
        config = wasmtime.Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        engine = wasmtime.Engine(config)
        self.store = wasmtime.Store(engine)
        self.store.set_fuel(50000000000)
        remaining = _remaining_run_seconds()
        self.store.set_epoch_deadline(max(1, int(remaining / _EPOCH_TICK_SECONDS)))
        _start_epoch_ticker(engine, time.monotonic() + remaining)
        self.module = wasmtime.Module(engine, wasm_bytes)
        self.linker = wasmtime.Linker(engine)
        self._define_host_funcs()
        self.instance = self.linker.instantiate(self.store, self.module)
        self.memory = self._require_export("memory")
        self.scratch_ptr_global = self._require_export("scratch_ptr")
        self.scratch_cap_global = self._require_export("scratch_cap")
        self.export_run_entry = self._require_export("run_entry")
        cap = self._global_value(self.scratch_cap_global)
        if cap < 0:
            raise RuntimeError("scratch_cap must be at least 0")

    def _define_host_funcs(self) -> None:
        wt = self.wasmtime
        self.linker.define(
            self.store,
            "env",
            "get_rand",
            wt.Func(
                self.store,
                wt.FuncType([wt.ValType.i32()], [wt.ValType.i64()]),
                lambda caller, p0: self._imp_get_rand(caller, p0),
                access_caller=True,
            ),
        )
        self.linker.define(
            self.store,
            "env",
            "run",
            wt.Func(
                self.store,
                wt.FuncType([wt.ValType.i32(), wt.ValType.i32(), wt.ValType.i32(), wt.ValType.i32(), wt.ValType.i32()], [wt.ValType.i32()]),
                lambda caller, p0, p1, p2, p3, p4: self._imp_run(caller, p0, p1, p2, p3, p4),
                access_caller=True,
            ),
        )
        self.linker.define(
            self.store,
            "env",
            "submit",
            wt.Func(
                self.store,
                wt.FuncType([wt.ValType.i32(), wt.ValType.i64(), wt.ValType.i64(), wt.ValType.i64()], [wt.ValType.i32()]),
                lambda caller, p0, p1, p2, p3: self._imp_submit(caller, p0, p1, p2, p3),
                access_caller=True,
            ),
        )
        self.linker.define(
            self.store,
            "env",
            "finalize",
            wt.Func(
                self.store,
                wt.FuncType([], [wt.ValType.i32()]),
                lambda caller: self._imp_finalize(caller),
                access_caller=True,
            ),
        )

    def _imp_get_rand(self, caller: Any, p0: int) -> int:
        if bool(getattr(self.sim, "success", False)):
            return -1
        _arg0 = int(p0)
        self.call_counts["get_rand"] += 1
        _res = self.sim.get_rand(_arg0)
        return int(_res)

    def _imp_run(self, caller: Any, p0: int, p1: int, p2: int, p3: int, p4: int) -> int:
        if bool(getattr(self.sim, "success", False)):
            return -1
        _arg0 = int(p0)
        _arg1 = self._read_memory(p1, p2)
        _arg2 = int(p3)
        self.call_counts["run"] += 1
        _res = self.sim.run(_arg0, _arg1, _arg2)
        _payload = bytes(_res)
        self.memory.write(self.store, _payload, p4)
        return len(_payload)

    def _imp_submit(self, caller: Any, p0: int, p1: int, p2: int, p3: int) -> int:
        if bool(getattr(self.sim, "success", False)):
            return -1
        _arg0 = int(p0)
        _arg1 = int(p1)
        _arg2 = int(p2)
        _arg3 = int(p3)
        self.call_counts["submit"] += 1
        _res = self.sim.submit(_arg0, _arg1, _arg2, _arg3)
        return int(_res)

    def _imp_finalize(self, caller: Any) -> int:
        if bool(getattr(self.sim, "success", False)):
            return -1
        self.call_counts["finalize"] += 1
        _res = self.sim.finalize()
        return int(_res)

    def _require_export(self, name: str) -> Any:
        value = self.instance.exports(self.store).get(name)
        if value is None:
            raise RuntimeError("missing required wasm export: " + name)
        return value

    def _global_value(self, global_obj: Any) -> int:
        value = global_obj.value(self.store)
        if not isinstance(value, int):
            raise RuntimeError("global must be i32")
        return value

    def _read_memory(self, ptr: int, length: int) -> bytes:
        if ptr < 0 or length < 0:
            raise RuntimeError("negative pointer or length")
        data = self.memory.read(self.store, ptr, ptr + length)
        if len(data) != length:
            raise RuntimeError("wasm memory read out of bounds")
        return bytes(data)

    def _write_scratch(self, payload: bytes) -> int:
        scratch_ptr = self._global_value(self.scratch_ptr_global)
        scratch_cap = self._global_value(self.scratch_cap_global)
        if len(payload) > scratch_cap:
            raise RuntimeError("host response exceeded scratch_cap")
        self.memory.write(self.store, payload, scratch_ptr)
        return len(payload)

    def _export_write(self, offset: int, payload: bytes) -> tuple[int, int]:
        scratch_ptr = self._global_value(self.scratch_ptr_global)
        scratch_cap = self._global_value(self.scratch_cap_global)
        end = offset + len(payload)
        if end > scratch_cap:
            raise RuntimeError("guest export args exceeded scratch_cap")
        self.memory.write(self.store, payload, scratch_ptr + offset)
        return (scratch_ptr + offset, end)

    def context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {}
        ctx["call_counts"] = self.call_counts
        ctx["success"] = bool(getattr(self.sim, "success", False))
        ctx["error"] = None
        return ctx

    def run(self) -> None:
        self.export_run_entry(self.store)


def unsuccessful(message: str) -> dict[str, Any]:
    return {
        "success": False,
        "score": None,
        "detail": None,
        "error": message,
    }


def normalize_result(
    sim: Any,
    counts: dict[str, int],
    host: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        _v = sim.success
    except Exception as exc:
        return unsuccessful("output success failed: " + sanitize_error(str(exc)))
    if not _is_bool(_v):
        return unsuccessful("output success has wrong type")
    out["success"] = _v
    try:
        _v = sim.score if sim.success else None
    except Exception as exc:
        return unsuccessful("output score failed: " + sanitize_error(str(exc)))
    if _v is not None and not _is_number(_v):
        return unsuccessful("output score has wrong type")
    out["score"] = _v
    try:
        _v = sim.detail
    except Exception as exc:
        return unsuccessful("output detail failed: " + sanitize_error(str(exc)))
    if _v is not None and not _is_string(_v):
        return unsuccessful("output detail has wrong type")
    out["detail"] = _v
    try:
        _v = sim.error
    except Exception as exc:
        return unsuccessful("output error failed: " + sanitize_error(str(exc)))
    if _v is not None and not _is_string(_v):
        return unsuccessful("output error has wrong type")
    out["error"] = _v
    return out


def run_per_team(request: dict[str, Any]) -> dict[str, Any]:
    host: WasmHost | None = None
    begin_run_deadline()
    try:
        sim = build_sim(request)
        wasm_bytes = request[WASM_FIELD]
        host = WasmHost(sim, wasm_bytes)
        host.run()
        return normalize_result(sim, host.call_counts, host.context(), request)
    except Exception as exc:
        if is_run_timeout(exc):
            return unsuccessful("run timed out")
        return unsuccessful(sanitize_error(str(exc)))
