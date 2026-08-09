"""Apply resource limits and exec a command in a fresh process."""

from __future__ import annotations

import os
import resource
import sys


def _set_memory_limit(limit: int) -> None:
    """Apply supported memory limits without breaking native macOS tools.

    Darwin exposes RLIMIT_RSS and RLIMIT_AS through Python but rejects attempts
    to set them.  Linux supports both and remains the production enforcement
    path; macOS still receives the CPU, process, and parent wall-clock limits.
    """
    if sys.platform == "darwin":
        return
    if hasattr(resource, "RLIMIT_RSS"):
        resource.setrlimit(resource.RLIMIT_RSS, (limit, limit))
    if hasattr(resource, "RLIMIT_AS"):
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def main() -> None:
    cpu_limit = int(sys.argv[1])
    memory_bytes = int(sys.argv[2])
    limit_processes = sys.argv[3] == "1"
    command = sys.argv[4:]

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    _set_memory_limit(memory_bytes)
    if limit_processes and hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
