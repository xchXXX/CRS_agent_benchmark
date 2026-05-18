"""Subprocess execution helpers."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShellResult:
    command: str
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def logs(self) -> str:
        if self.stderr:
            return f"{self.stdout}\n{self.stderr}".strip()
        return self.stdout.strip()


def _execution_env(*, sanitize_env: bool) -> dict[str, str]:
    if not sanitize_env:
        return os.environ.copy()

    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONPATH",
        "SHELL",
        "TMPDIR",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int,
    sanitize_env: bool,
) -> ShellResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=str(cwd),
            env=_execution_env(sanitize_env=sanitize_env),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return ShellResult(
            command=command,
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return ShellResult(
            command=command,
            cwd=str(cwd),
            exit_code=124,
            stdout=stdout,
            stderr=stderr or f"Command timed out after {timeout_seconds}s",
            duration_seconds=time.monotonic() - start,
            timed_out=True,
        )

