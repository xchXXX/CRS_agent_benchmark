"""Harness result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HarnessResult:
    passed: bool
    exit_code: int
    summary: str
    public_logs: str
    raw_logs: str
    failed_tests: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

