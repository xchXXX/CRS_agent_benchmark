"""Lifecycle nodes for the coding-engine graph."""

from __future__ import annotations

from app.coding_engine.config import (
    DEFAULT_HARNESS_COMMAND,
    DEFAULT_HARNESS_TIMEOUT_SECONDS,
    DEFAULT_MAX_ITERATIONS,
)
from app.coding_engine.persistence import ensure_run_root
from app.coding_engine.sandbox.workspace import new_run_id, prepare_workspace
from app.coding_engine.state import CodingEngineState


def bootstrap_node(state: CodingEngineState) -> CodingEngineState:
    run_id = state.get("run_id") or new_run_id()
    run_root = ensure_run_root(run_id, state.get("run_root"))
    return {
        "run_id": run_id,
        "run_root": str(run_root),
        "iteration": int(state.get("iteration", 0)),
        "max_iterations": int(state.get("max_iterations", DEFAULT_MAX_ITERATIONS)),
        "harness_timeout_seconds": int(
            state.get("harness_timeout_seconds", DEFAULT_HARNESS_TIMEOUT_SECONDS)
        ),
        "harness_command": state.get("harness_command") or DEFAULT_HARNESS_COMMAND,
        "sandbox_enabled": bool(state.get("sandbox_enabled", False)),
        "sanitize_env": bool(state.get("sanitize_env", True)),
        "auto_apply_patch": bool(state.get("auto_apply_patch", False)),
        "allow_unsandboxed_apply": bool(state.get("allow_unsandboxed_apply", False)),
        "persistence_enabled": bool(state.get("persistence_enabled", True)),
        "status": "planning",
        "phase": "bootstrap",
        "events": [
            {
                "phase": "bootstrap",
                "message": "Coding engine run initialized.",
                "detail": {"run_id": run_id, "run_root": str(run_root)},
            }
        ],
    }


def prepare_workspace_node(state: CodingEngineState) -> CodingEngineState:
    workspace = prepare_workspace(
        run_id=state["run_id"],
        workspace_path=state.get("workspace_path"),
        sandbox_enabled=bool(state.get("sandbox_enabled", False)),
    )
    return {
        "workspace_path": str(workspace),
        "phase": "prepare_workspace",
        "events": [
            {
                "phase": "prepare_workspace",
                "message": "Workspace prepared.",
                "detail": {
                    "workspace_path": str(workspace),
                    "sandbox_enabled": bool(state.get("sandbox_enabled", False)),
                },
            }
        ],
    }


def human_gate_node(state: CodingEngineState) -> CodingEngineState:
    return {
        "status": "needs_human",
        "phase": "human_gate",
        "events": [
            {
                "phase": "human_gate",
                "message": "Patch proposal is ready for human review.",
                "detail": {"auto_apply_patch": bool(state.get("auto_apply_patch", False))},
            }
        ],
    }
