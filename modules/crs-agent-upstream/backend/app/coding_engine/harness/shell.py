"""Shell-command harness implementation."""

from __future__ import annotations

from pathlib import Path

from app.coding_engine.harness.base import HarnessResult
from app.coding_engine.sandbox.executor import run_shell_command


MAX_PUBLIC_LOG_CHARS = 12000


def _tail(value: str, max_chars: int = MAX_PUBLIC_LOG_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _failed_tests_from_logs(logs: str) -> list[str]:
    failed: list[str] = []
    for line in logs.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED ") or " FAILED " in stripped:
            failed.append(stripped)
        elif stripped.startswith("ERROR ") or " ERROR " in stripped:
            failed.append(stripped)
    return failed[:25]


def run_shell_harness(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int,
    sanitize_env: bool,
) -> HarnessResult:
    shell_result = run_shell_command(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        sanitize_env=sanitize_env,
    )
    logs = shell_result.logs
    failed_tests = _failed_tests_from_logs(logs)
    passed = shell_result.exit_code == 0
    if shell_result.timed_out:
        summary = f"Harness timed out after {timeout_seconds}s."
    elif passed:
        summary = "Harness passed."
    else:
        summary = f"Harness failed with exit code {shell_result.exit_code}."

    return HarnessResult(
        passed=passed,
        exit_code=shell_result.exit_code,
        summary=summary,
        public_logs=_tail(logs),
        raw_logs=logs,
        failed_tests=failed_tests,
        duration_seconds=shell_result.duration_seconds,
    )

