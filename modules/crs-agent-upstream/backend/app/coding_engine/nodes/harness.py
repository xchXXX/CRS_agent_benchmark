"""Harness execution and judging nodes."""

from __future__ import annotations

from pathlib import Path

from app.coding_engine.harness.shell import run_shell_harness
from app.coding_engine.state import CodingEngineState


def run_harness_node(state: CodingEngineState) -> CodingEngineState:
    iteration = int(state.get("iteration", 0)) + 1
    workspace = Path(state["workspace_path"])
    command = state["harness_command"]
    result = run_shell_harness(
        command,
        cwd=workspace,
        timeout_seconds=int(state.get("harness_timeout_seconds", 120)),
        sanitize_env=bool(state.get("sanitize_env", True)),
    )
    return {
        "iteration": iteration,
        "status": "running_harness",
        "phase": "run_harness",
        "passed": result.passed,
        "exit_code": result.exit_code,
        "harness_summary": result.summary,
        "public_logs": result.public_logs,
        "raw_logs": result.raw_logs,
        "failed_tests": result.failed_tests,
        "attempts": [
            {
                "iteration": iteration,
                "command": command,
                "cwd": str(workspace),
                "passed": result.passed,
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "summary": result.summary,
                "failed_tests": result.failed_tests,
            }
        ],
        "events": [
            {
                "phase": "run_harness",
                "message": result.summary,
                "detail": {"iteration": iteration, "exit_code": result.exit_code},
            }
        ],
    }


def judge_node(state: CodingEngineState) -> CodingEngineState:
    if state.get("passed"):
        status = "passed"
        message = "Harness passed; coding run is complete."
    elif int(state.get("iteration", 0)) >= int(state.get("max_iterations", 1)):
        status = "failed"
        message = "Harness still failing after max iterations."
    else:
        status = "reflecting"
        message = "Harness failed; proceeding to reflection."

    return {
        "status": status,
        "phase": "judge",
        "events": [{"phase": "judge", "message": message, "detail": {"status": status}}],
    }


def route_after_judge(state: CodingEngineState) -> str:
    if state.get("status") in {"passed", "failed", "error"}:
        return "finish"
    return "reflect"

