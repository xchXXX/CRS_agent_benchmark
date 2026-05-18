"""State contract for the harness-driven coding engine graph."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict


EngineStatus = Literal[
    "pending",
    "planning",
    "running_harness",
    "reflecting",
    "coding",
    "applying_patch",
    "needs_human",
    "passed",
    "failed",
    "error",
]


class HarnessAttempt(TypedDict, total=False):
    iteration: int
    command: str
    cwd: str
    passed: bool
    exit_code: int
    duration_seconds: float
    summary: str
    failed_tests: list[str]


class EngineEvent(TypedDict, total=False):
    phase: str
    message: str
    detail: dict[str, Any]


class CodingEngineState(TypedDict, total=False):
    # Inputs
    task: str
    requirements: str
    harness_command: str
    workspace_path: str
    max_iterations: int
    harness_timeout_seconds: int
    sandbox_enabled: bool
    sanitize_env: bool
    auto_apply_patch: bool
    allow_unsandboxed_apply: bool
    operator_messages: list[str]
    persistence_enabled: bool
    run_root: str

    # Runtime bookkeeping
    run_id: str
    iteration: int
    status: EngineStatus
    phase: str

    # Agent-visible working fields
    plan: str
    reflection: str
    proposed_patch: str
    applied_patch: bool

    # Harness outputs
    passed: bool
    exit_code: int
    harness_summary: str
    public_logs: str
    raw_logs: str
    failed_tests: list[str]
    latest_persistence_path: str
    persisted_event_count: int
    persisted_attempt_count: int

    # Accumulated observability data
    attempts: Annotated[list[HarnessAttempt], add]
    events: Annotated[list[EngineEvent], add]
