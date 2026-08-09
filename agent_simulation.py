"""Stateful, tool-friendly access to the production simulation and scorer.

The Wasm ABI deliberately remains unchanged.  This module wraps the same
``LiveEvaluationSim`` used by the visualizer so an external agent can retain a
seed across dependent exploratory runs and receive structured score data.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from golduck.levels import LEVELS
from golduck.sim import Sim
from visualizer_eval import (
    VISUALIZER_TIMEOUT_SECONDS,
    LiveEvaluationSim,
    parse_seed,
)


class AgentSimulationError(ValueError):
    """Raised when an agent simulation request is malformed."""


class UnknownAgentSimulationSession(AgentSimulationError):
    """Raised when an agent references a missing or expired session."""


class AgentEvaluationSim(LiveEvaluationSim):
    """Lean scorer for agents without visualizer event or cell-list overhead."""

    def __init__(self, seed: bytes) -> None:
        super().__init__(seed, lambda _event: None)

    def run(self, level: int, pattern: bytes, generations: int) -> bytes:
        level_run = self._run_counts.get(level, 0) + 1
        # Call the production implementation directly. LiveEvaluationSim.run
        # parses the input a second time and expands every live cell into an
        # [x, y] list solely for browser visualization.
        output = Sim.run(self, level, pattern, generations)
        cell_count, recorded_generations = self._run_stats[level][-1]
        self.runs.append(
            {
                "level": level,
                "level_run": level_run,
                "generations": recorded_generations,
                "cell_count": cell_count,
            }
        )
        return output

    def submit(self, level: int, value: int, known_mask: int, guess_mask: int) -> bool:
        # Submission events are visualizer-only; scoring state lives in Sim.
        return Sim.submit(self, level, value, known_mask, guess_mask)


SimulationFactory = Callable[[bytes], LiveEvaluationSim]


def _new_simulation(seed: bytes) -> LiveEvaluationSim:
    return AgentEvaluationSim(seed)


@dataclass(slots=True)
class _AgentSession:
    session_id: str
    seed_hex: str
    simulation: LiveEvaluationSim
    lock: threading.RLock = field(default_factory=threading.RLock)


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AgentSimulationError(f"{key} must be a non-empty string")
    return value


def _required_integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AgentSimulationError(f"{key} must be an integer")
    return value


def _integer_or_string(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise AgentSimulationError(f"{key} must be an integer or integer string")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            pass
    raise AgentSimulationError(f"{key} must be an integer or integer string")


def _level_capabilities() -> list[dict[str, object]]:
    capabilities: list[dict[str, object]] = []
    for level_id, level_type in sorted(LEVELS.items()):
        level = level_type(0)
        min_generations, max_generations = level.get_generation_range()
        _, _, contestant_width, contestant_height = level.get_contestant_rect()
        capabilities.append(
            {
                "level": level_id,
                "max_runs": level.get_max_runs(),
                "generations": {
                    "min": min_generations,
                    "max": max_generations,
                },
                "contestant_size": {
                    "width": contestant_width,
                    "height": contestant_height,
                },
            }
        )
    return capabilities


def _public_level_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    """Return a useful score breakdown without disclosing the hidden secret."""
    public = {key: value for key, value in snapshot.items() if key != "secret"}
    final_score = public.get("score")
    submitted = bool(public.get("submitted"))
    if submitted and isinstance(final_score, (int, float)):
        score = float(final_score)
        score_type = "submitted"
    else:
        score = float(public["performance_score"])
        score_type = "performance"
    public["final_score"] = final_score
    public["score"] = score
    public["score_type"] = score_type
    public["runs_remaining"] = max(
        0,
        int(public["max_runs"]) - int(public["runs_completed"]),
    )
    return public


def _score_state(
    simulation: LiveEvaluationSim,
) -> tuple[dict[int, dict[str, object]], dict[str, object]]:
    """Compute every level snapshot once and derive the aggregate totals."""
    snapshots = {level: simulation.level_snapshot(level) for level in sorted(LEVELS)}
    values = list(snapshots.values())
    totals: dict[str, object] = {
        "score": sum(
            float(snapshot["score"])
            for snapshot in values
            if snapshot["score"] is not None
        ),
        "potential_score": sum(
            float(snapshot["potential_score"]) for snapshot in values
        ),
        "submitted_levels": sum(bool(snapshot["submitted"]) for snapshot in values),
        "level_count": len(values),
    }
    return snapshots, totals


class AgentSimulationService:
    """Own bounded, thread-safe sessions for multi-run agent exploration."""

    def __init__(
        self,
        simulation_factory: SimulationFactory = _new_simulation,
        *,
        max_sessions: int = 128,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._simulation_factory = simulation_factory
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, _AgentSession] = OrderedDict()
        self._lock = threading.RLock()

    def handle(self, payload: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(payload, Mapping):
            raise AgentSimulationError("payload must be a JSON object")
        action_value = payload.get("action")
        if not isinstance(action_value, str):
            raise AgentSimulationError("action must be a string")
        action = action_value.strip().lower()

        if action == "start":
            return self._start(payload)
        if action == "run":
            return self._run(payload)
        if action == "submit":
            return self._submit(payload)
        if action == "finalize":
            return self._finalize(payload)
        if action == "status":
            return self._status(payload)
        if action == "close":
            return self._close(payload)
        raise AgentSimulationError(
            "action must be one of start, run, submit, finalize, status, or close"
        )

    def _start(self, payload: Mapping[str, object]) -> dict[str, object]:
        seed_value = payload.get("seed")
        if seed_value is None:
            seed = secrets.token_bytes(16)
        elif isinstance(seed_value, str):
            try:
                seed = parse_seed(seed_value)
            except ValueError as exc:
                raise AgentSimulationError(str(exc)) from None
        else:
            raise AgentSimulationError("seed must be a 128-bit hexadecimal string")

        session_id = secrets.token_urlsafe(18)
        session = _AgentSession(
            session_id=session_id,
            seed_hex=seed.hex(),
            simulation=self._simulation_factory(seed),
        )
        with self._lock:
            while len(self._sessions) >= self._max_sessions:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = session

        return {
            "action": "start",
            "session_id": session_id,
            "seed": session.seed_hex,
            "levels": _level_capabilities(),
        }

    def _get_session(self, payload: Mapping[str, object]) -> _AgentSession:
        session_id = _required_string(payload, "session_id")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise UnknownAgentSimulationSession(
                    f"unknown or expired agent simulation session {session_id!r}"
                )
            self._sessions.move_to_end(session_id)
            return session

    @staticmethod
    def _base_response(session: _AgentSession, action: str) -> dict[str, object]:
        return {
            "action": action,
            "session_id": session.session_id,
            "seed": session.seed_hex,
            "finalized": bool(session.simulation.success),
        }

    @staticmethod
    def _touched_levels(simulation: LiveEvaluationSim) -> list[int]:
        return sorted(set(simulation._run_counts) | set(simulation._submissions))

    def _run(self, payload: Mapping[str, object]) -> dict[str, object]:
        session = self._get_session(payload)
        level = _required_integer(payload, "level")
        generations = _required_integer(payload, "generations")
        pattern_value = payload.get("pattern", payload.get("rle"))
        if not isinstance(pattern_value, str):
            raise AgentSimulationError("pattern must be an RLE string")
        try:
            pattern = pattern_value.encode("ascii")
        except UnicodeEncodeError:
            raise AgentSimulationError("pattern must contain ASCII RLE data") from None

        with session.lock:
            simulation = session.simulation
            # The visualizer timeout is per evaluation.  Interactive sessions
            # can live much longer, so give each individual agent run the same
            # bounded computation window.
            simulation.deadline = time.monotonic() + VISUALIZER_TIMEOUT_SECONDS
            output = simulation.run(level, pattern, generations)
            snapshots, totals = _score_state(simulation)
            breakdown = _public_level_snapshot(snapshots[level])
            run = simulation.runs[-1]
            response = self._base_response(session, "run")
            response.update(
                {
                    "level": level,
                    "run": int(run["level_run"]),
                    "generations": generations,
                    "input_cell_count": int(run["cell_count"]),
                    "output_rle": output.decode("ascii"),
                    "score": breakdown["score"],
                    "score_type": breakdown["score_type"],
                    "score_breakdown": breakdown,
                    "totals": totals,
                }
            )
            return response

    def _submit(self, payload: Mapping[str, object]) -> dict[str, object]:
        session = self._get_session(payload)
        level = _required_integer(payload, "level")
        value = _integer_or_string(payload, "value")
        known_mask = _integer_or_string(payload, "known_mask")
        guess_mask = _integer_or_string(payload, "guess_mask")

        with session.lock:
            simulation = session.simulation
            accepted = bool(simulation.submit(level, value, known_mask, guess_mask))
            snapshots, totals = _score_state(simulation)
            breakdown = _public_level_snapshot(snapshots[level])
            response = self._base_response(session, "submit")
            response.update(
                {
                    "level": level,
                    "accepted": accepted,
                    "score": breakdown["score"],
                    "score_type": breakdown["score_type"],
                    "score_breakdown": breakdown,
                    "totals": totals,
                }
            )
            return response

    def _finalize(self, payload: Mapping[str, object]) -> dict[str, object]:
        session = self._get_session(payload)
        with session.lock:
            simulation = session.simulation
            simulation.finalize()
            snapshots, totals = _score_state(simulation)
            levels = [
                _public_level_snapshot(snapshots[level])
                for level in self._touched_levels(simulation)
            ]
            response = self._base_response(session, "finalize")
            response.update(
                {
                    "score": float(simulation.score),
                    "score_breakdown": levels,
                    "totals": totals,
                }
            )
            return response

    def _status(self, payload: Mapping[str, object]) -> dict[str, object]:
        session = self._get_session(payload)
        with session.lock:
            simulation = session.simulation
            snapshots, totals = _score_state(simulation)
            levels = [
                _public_level_snapshot(snapshots[level])
                for level in self._touched_levels(simulation)
            ]
            response = self._base_response(session, "status")
            response.update(
                {
                    "score": float(totals["score"]),
                    "score_breakdown": levels,
                    "totals": totals,
                }
            )
            return response

    def _close(self, payload: Mapping[str, object]) -> dict[str, object]:
        session = self._get_session(payload)
        with session.lock:
            with self._lock:
                self._sessions.pop(session.session_id, None)
            return {
                "action": "close",
                "session_id": session.session_id,
                "closed": True,
            }


AGENT_SIMULATION_SERVICE = AgentSimulationService()


def agent_simulate_and_score(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Agent-callable function for stateful simulation, exploration, and scoring.

    Start with ``{"action": "start", "seed": "<32 hex>"}``, then retain the
    returned ``session_id`` for ``run``, ``submit``, ``status``, ``finalize``,
    and ``close`` actions.
    """
    return AGENT_SIMULATION_SERVICE.handle(payload)


__all__ = [
    "AgentEvaluationSim",
    "AgentSimulationError",
    "AgentSimulationService",
    "UnknownAgentSimulationSession",
    "agent_simulate_and_score",
]
