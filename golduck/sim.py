"""Challenge simulator."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    ChallengeError,
    ExecutionError,
    InvalidPatternError,
    InvalidSubmissionError,
    RunLimitError,
)
from .level import Level
from .levels import LEVELS
from .rle import (
    RLEPattern,
    encode_rle,
    extract_rect,
    merge_placements,
    parse_rle,
    pattern_from_cells,
)

DEFAULT_CPU_SECONDS = 30.0
DEFAULT_MEMORY_BYTES = 100 * 1024 * 1024
DEFAULT_WALL_SECONDS = 30.0
DISABLE_SANDBOX_ENV = "DISABLE_SANDBOX"


def _default_bgolly_path() -> Path:
    """Select the native bgolly executable for the current platform.

    The repository's bundled binary is built for the Linux runner.  Local
    macOS development uses the universal bgolly installed by Golly instead.
    Explicit constructor and ``BGOLLY`` values still take precedence.
    """
    bundled = Path(__file__).resolve().parent.parent / "bgolly"
    if sys.platform == "darwin":
        native = shutil.which("bgolly")
        if native:
            return Path(native)
    return bundled


def iu64_to_u64(value: int):
    """Convert a number (either i64 or u64) to u64."""
    if value < 0:
        value += 1 << 64
    if value < 0 or value >= 1 << 64:
        raise InvalidSubmissionError(
            "submitted value must be a 64-bit unsigned integer"
        )
    return value


@dataclass(frozen=True)
class Submission:
    secret: int
    submission: int
    known_mask: int
    guess_mask: int


class Sim:
    def __init__(
        self,
        seed_hex: bytes,
        team_id: int | None = None,
        enabled_stages: int | None = None,
        *,
        bgolly_path: str | os.PathLike[str] | None = None,
        nsjail_path: str | os.PathLike[str] | None = None,
        cpu_seconds: float = DEFAULT_CPU_SECONDS,
        memory_bytes: int = DEFAULT_MEMORY_BYTES,
        wall_seconds: float = DEFAULT_WALL_SECONDS,
    ):
        if not isinstance(seed_hex, bytes):
            raise ChallengeError("seed_hex must be bytes")
        self.seed = seed_hex
        self.bgolly_path = Path(
            bgolly_path
            or os.environ.get("BGOLLY")
            or _default_bgolly_path()
        )
        self.nsjail_path = Path(
            nsjail_path
            or os.environ.get("NSJAIL")
            or Path(__file__).resolve().parent.parent / "nsjail"
        )
        if enabled_stages is None:
            # enable all stages
            enabled_stages = -1
        self.enabled_stages = enabled_stages
        self.cpu_seconds = float(cpu_seconds)
        self.memory_bytes = int(memory_bytes)
        self.wall_seconds = float(wall_seconds)
        self._levels: dict[int, Level] = {}
        self._run_counts: dict[int, int] = {}
        self._run_stats: dict[int, list[tuple[int, int]]] = {}
        self._submissions: dict[int, Submission] = {}
        self._cpu_used = 0.0

        self.success = False
        self.score = 0.0
        self.detail = None
        self.error = None

    def get_rand(self, salt: int) -> int:
        self._ensure_open()
        digest = hmac.new(
            self.seed, f"user_rand_{salt}".encode("ascii"), hashlib.sha256
        ).digest()
        return int.from_bytes(digest[:8], "little")

    def run(self, level: int, pattern: bytes, generations: int) -> bytes:
        self._ensure_open()
        pattern = pattern.decode("ascii")
        level_obj = self._get_level(level)
        if level in self._submissions:
            raise RunLimitError("no further runs accepted after submission")
        if self._run_counts.get(level, 0) >= level_obj.get_max_runs():
            raise RunLimitError(
                f"level {level} allows at most {level_obj.get_max_runs()} run(s)"
            )
        if not isinstance(generations, int) or isinstance(generations, bool):
            raise ChallengeError("generations must be an integer")
        min_gen, max_gen = level_obj.get_generation_range()
        if generations < min_gen or generations > max_gen:
            raise ChallengeError(
                f"generations must satisfy {min_gen} <= generations <= {max_gen}"
            )

        contestant_pattern = parse_rle(pattern)
        contestant_rect = level_obj.get_contestant_rect()
        if (
            contestant_pattern.width > contestant_rect[2]
            or contestant_pattern.height > contestant_rect[3]
        ):
            raise InvalidPatternError(
                "contestant pattern exceeds maximum size "
                f"{contestant_rect[2]}x{contestant_rect[3]}"
            )

        combined = self._build_combined_pattern(level_obj, contestant_pattern)
        self._run_counts[level] = self._run_counts.get(level, 0) + 1
        self._run_stats.setdefault(level, []).append(
            (_live_cell_count(contestant_pattern), generations)
        )
        output = self._run_bgolly(level_obj, combined, generations)
        # Portable in-process engines can return their already-validated
        # sparse pattern directly. External bgolly output remains text and
        # still goes through strict header parsing below.
        output_pattern = (
            output
            if isinstance(output, RLEPattern)
            else parse_rle(output, require_header=True)
        )
        canvas_rect = level_obj.get_canvas_rect()
        if (output_pattern.width, output_pattern.height) != canvas_rect[2:4]:
            raise ExecutionError("challenge pattern exceeded canvas bounds")
        if not _has_corner_markers(output_pattern):
            raise ExecutionError("challenge pattern failed corner integrity check")
        cropped = extract_rect(
            output_pattern, canvas_rect, level_obj.get_viewing_rect()
        )
        return encode_rle(cropped).encode("ascii")

    def submit(self, level: int, value: int, known_mask: int, guess_mask: int) -> bool:
        self._ensure_open()
        level_obj = self._get_level(level)
        if level in self._submissions:
            raise InvalidSubmissionError("only one submission is allowed per level")
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidSubmissionError("submitted value must be an integer")
        value = iu64_to_u64(value)
        known_mask = iu64_to_u64(known_mask)
        guess_mask = iu64_to_u64(guess_mask)
        if known_mask & guess_mask:
            raise InvalidSubmissionError("known_mask and guess_mask cannot overlap")
        self._submissions[level] = Submission(
            level_obj.secret, value, known_mask, guess_mask
        )
        correct = known_mask and (
            (known_mask & value) == (known_mask & level_obj.secret)
        )
        return correct

    def finalize(self) -> int:
        self._ensure_open()

        level_details: dict[str, object] = {}
        total_score = 0.0
        attempted = 0

        for level, submission in sorted(self._submissions.items()):
            level_obj = self._get_level(level)
            stats = self._run_stats.get(level, [])
            level_score, score_detail = _score_submission(level_obj, stats, submission)

            total_score += level_score
            attempted += 1
            level_details[str(level)] = score_detail

        self.success = True
        self.score = total_score
        self.detail = json.dumps({"levels": level_details, "attempted": attempted})
        self.error = None
        return int(self.success)

    def _ensure_open(self) -> None:
        if self.success:
            raise ChallengeError("session already finalized")

    def _get_level(self, level: int) -> Level:
        if (0 <= level <= 31) and not self.enabled_stages & (1 << level):
            raise ChallengeError(f"unknown level {level}")
        if level not in LEVELS:
            raise ChallengeError(f"unimplemented level {level} (should not happen)")
        if level not in self._levels:
            digest = hmac.new(
                self.seed, f"secret_{level}".encode("ascii"), hashlib.sha256
            ).digest()
            secret = int.from_bytes(digest[:8], "little")
            self._levels[level] = LEVELS[level](secret)
        return self._levels[level]

    def _build_combined_pattern(
        self, level_obj: Level, contestant_pattern: RLEPattern
    ) -> RLEPattern:
        canvas = level_obj.get_canvas_rect()
        secret_x, secret_y, secret_rle = level_obj.get_secret()
        contestant_x, contestant_y, _, _ = level_obj.get_contestant_rect()
        corner_pattern = _corner_blocks(canvas[2], canvas[3])
        placements: list[tuple[int, int, str | RLEPattern]] = [
            (canvas[0], canvas[1], corner_pattern),
            (secret_x, secret_y, secret_rle),
            (contestant_x, contestant_y, contestant_pattern),
        ]
        return merge_placements(placements, canvas)

    def _run_bgolly(
        self, level_obj: Level, pattern: RLEPattern, generations: int
    ) -> str:
        if not self.bgolly_path.exists():
            raise ExecutionError(f"bgolly binary not found at {self.bgolly_path}")
        if not _sandbox_disabled() and not self.nsjail_path.exists():
            raise ExecutionError(f"nsjail binary not found at {self.nsjail_path}")
        remaining_cpu = self.cpu_seconds - self._cpu_used
        if remaining_cpu <= 0:
            raise ExecutionError("bgolly exceeded CPU limit")
        cpu_limit = max(1, math.ceil(remaining_cpu))

        with tempfile.TemporaryDirectory(prefix="golduck_") as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            input_path = tmp_path / "pattern.rle"
            output_path = tmp_path / "output.rle"
            input_path.write_text(encode_rle(pattern), encoding="ascii")

            completed, child_cpu, timed_out = _run_limited_process(
                self._bgolly_command(tmp_path, generations),
                cpu_limit=cpu_limit,
                memory_bytes=self.memory_bytes,
                wall_seconds=self.wall_seconds,
                limit_processes=_sandbox_disabled(),
            )
            self._cpu_used += child_cpu
            if timed_out:
                raise ExecutionError("bgolly exceeded wall clock timeout")

            if completed.returncode != 0:
                raise self._classify_bgolly_failure(completed, cpu_limit, child_cpu)
            if not output_path.exists():
                raise ExecutionError("bgolly did not produce an output RLE")
            output_text = output_path.read_text(encoding="ascii")
        return output_text

    def _bgolly_command(self, tmp_path: Path, generations: int) -> list[str]:
        if _sandbox_disabled():
            return [
                str(self.bgolly_path),
                "-q",
                "-q",
                "-m",
                str(generations),
                "-o",
                str(tmp_path / "output.rle"),
                str(tmp_path / "pattern.rle"),
            ]
        return [
            str(self.nsjail_path),
            "-Mo",
            "--really_quiet",
            "-R",
            "/lib/x86_64-linux-gnu",
            "-R",
            "/lib64",
            "-R",
            f"{self.bgolly_path}:/bin/bgolly",
            "-B",
            f"{tmp_path}:/tmp",
            "--rlimit_as",
            "soft",
            "--rlimit_cpu",
            "soft",
            "--rlimit_nproc",
            "0",
            "--",
            "/bin/bgolly",
            "-q",
            "-q",
            "-m",
            str(generations),
            "-o",
            "/tmp/output.rle",
            "/tmp/pattern.rle",
        ]

    def _classify_bgolly_failure(
        self,
        completed: subprocess.CompletedProcess[str],
        cpu_limit: int,
        child_cpu: float,
    ) -> ExecutionError:
        returncode = completed.returncode
        if returncode < 0 or returncode >= 128:
            if returncode >= 128:
                sig = returncode - 128
            else:
                sig = -returncode
            if sig == signal.SIGXCPU or (
                sig == signal.SIGKILL and child_cpu >= cpu_limit - 0.1
            ):
                return ExecutionError("bgolly exceeded CPU limit")
            if sig in {signal.SIGKILL, signal.SIGSEGV}:
                return ExecutionError("bgolly exceeded memory limit")
            return ExecutionError(f"bgolly crashed (signal {sig})")
        return ExecutionError(f"bgolly crashed (status code {returncode})")


def _corner_blocks(width: int, height: int) -> RLEPattern:
    cells = {cell for corner in _corner_regions(width, height) for cell in corner}
    return pattern_from_cells(cells, width, height)


def _corner_regions(width: int, height: int) -> tuple[tuple[tuple[int, int], ...], ...]:
    return (
        ((0, 0), (1, 0), (0, 1), (1, 1)),
        ((width - 2, 0), (width - 1, 0), (width - 2, 1), (width - 1, 1)),
        ((0, height - 2), (1, height - 2), (0, height - 1), (1, height - 1)),
        (
            (width - 2, height - 2),
            (width - 1, height - 2),
            (width - 2, height - 1),
            (width - 1, height - 1),
        ),
    )


def _has_corner_markers(pattern: RLEPattern) -> bool:
    if pattern.width < 2 or pattern.height < 2:
        return False
    corners = _corner_regions(pattern.width, pattern.height)
    return all(any(pattern.has_cell(x, y) for x, y in corner) for corner in corners)


def _live_cell_count(pattern: RLEPattern) -> int:
    return sum(
        end - start for intervals in pattern.rows.values() for start, end in intervals
    )


def _score_level(
    level_obj: Level, stats: list[tuple[int, int]]
) -> tuple[float, dict[str, object]]:
    num_runs = len(stats)
    contestant_rect = level_obj.get_contestant_rect()
    contestant_rect_area = contestant_rect[2] * contestant_rect[3]
    _, max_generations = level_obj.get_generation_range()

    runs_score = 50000.0 * 2.0 / (max(num_runs, 1) + 1)
    density_ratio = max(
        (
            _clamp01(math.log(num_live_pixels + 1) / math.log(contestant_rect_area + 1))
            for num_live_pixels, _ in stats
        ),
        default=0.0,
    )
    generations_ratio = max(
        (
            _clamp01(math.log(num_generations + 1) / math.log(max_generations + 1))
            for _, num_generations in stats
        ),
        default=0.0,
    )
    density_score = 25000.0 * (1.0 - density_ratio)
    generations_score = 25000.0 * (1.0 - generations_ratio)
    bonus = runs_score + density_score + generations_score
    score = 900000.0 + bonus
    return score, {
        "run_bonus": runs_score,
        "density_bonus": density_score,
        "generations_bonus": generations_score,
    }


def _score_submission(
    level_obj: Level,
    stats: list[tuple[int, int]],
    submission: Submission,
) -> tuple[float, dict[str, object]]:
    """Score one submitted level using the same details exposed by finalize()."""
    wrong_bits = submission.submission ^ submission.secret
    # Correct known bits are all-or-nothing: one wrong known bit zeros the
    # entire known-mask weight.
    known_correct = (
        bool(submission.known_mask) and (wrong_bits & submission.known_mask) == 0
    )
    known_weight = int(known_correct) * (submission.known_mask.bit_count() / 64)
    # Guessed bits receive partial credit and can have negative weight.
    guess_weight = (
        0.5
        * (
            0.4 * (~wrong_bits & submission.guess_mask).bit_count()
            - 0.6 * (wrong_bits & submission.guess_mask).bit_count()
        )
        / 64
    )

    level_score, score_detail = _score_level(level_obj, stats)
    score_detail["known_weight"] = known_weight
    score_detail["guess_weight"] = guess_weight
    level_score *= known_weight + guess_weight
    if known_correct and submission.known_mask == ((1 << 64) - 1):
        level_score += 100_000
    score_detail["score"] = level_score
    return level_score, score_detail


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _sandbox_disabled() -> bool:
    # nsjail is Linux-only.  On macOS, bgolly still runs through the resource
    # limiting helper below, but cannot be wrapped by the bundled ELF nsjail.
    return sys.platform != "linux" or os.environ.get(
        DISABLE_SANDBOX_ENV, ""
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _run_limited_process(
    command: list[str],
    *,
    cpu_limit: int,
    memory_bytes: int,
    wall_seconds: float,
    limit_processes: bool,
) -> tuple[subprocess.CompletedProcess[str], float, bool]:
    """Run one child and return resource usage for that specific PID."""
    helper = Path(__file__).with_name("_exec_limited.py")
    limited_command = [
        sys.executable,
        str(helper),
        str(cpu_limit),
        str(memory_bytes),
        "1" if limit_processes else "0",
        *command,
    ]

    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        process = subprocess.Popen(
            limited_command,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        deadline = time.monotonic() + wall_seconds
        timed_out = False
        try:
            while True:
                waited_pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if waited_pid:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    try:
                        os.kill(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    _, status, usage = os.wait4(process.pid, 0)
                    break
                time.sleep(min(0.01, remaining))
        except BaseException:
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(process.pid, 0)
            except ChildProcessError:
                pass
            raise

        process.returncode = os.waitstatus_to_exitcode(status)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode(errors="replace")
        stderr = stderr_file.read().decode(errors="replace")

    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
    child_cpu = usage.ru_utime + usage.ru_stime
    return completed, child_cpu, timed_out
