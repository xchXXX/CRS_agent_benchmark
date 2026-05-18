"""State scoping utilities.

The coding node must only receive the public harness view. Private evaluator
fields can exist in state later, but they must not be copied into this view.
"""

from __future__ import annotations

from typing import Any

from app.coding_engine.state import CodingEngineState


CODER_VISIBLE_KEYS = {
    "task",
    "requirements",
    "iteration",
    "max_iterations",
    "plan",
    "reflection",
    "harness_summary",
    "public_logs",
    "failed_tests",
    "operator_messages",
}


def coder_view(state: CodingEngineState) -> dict[str, Any]:
    return {key: state[key] for key in CODER_VISIBLE_KEYS if key in state}


def evaluator_view(state: CodingEngineState) -> dict[str, Any]:
    return {
        "task": state.get("task", ""),
        "requirements": state.get("requirements", ""),
        "harness_command": state.get("harness_command", ""),
        "workspace_path": state.get("workspace_path", ""),
        "raw_logs": state.get("raw_logs", ""),
        "exit_code": state.get("exit_code"),
        "failed_tests": state.get("failed_tests", []),
    }

