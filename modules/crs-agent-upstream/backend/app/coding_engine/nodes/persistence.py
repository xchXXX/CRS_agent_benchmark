"""Persistence nodes for local-only coding-engine observability."""

from __future__ import annotations

from app.coding_engine.persistence import (
    append_attempt_log,
    append_event_log,
    ensure_run_root,
    persist_operator_messages,
    persist_patch_proposal,
    persist_raw_logs,
    persist_state_snapshot,
)
from app.coding_engine.state import CodingEngineState


def persist_run_node(state: CodingEngineState) -> CodingEngineState:
    if not state.get("persistence_enabled", True):
        return {}

    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    persisted_event_count = int(state.get("persisted_event_count", 0))
    persisted_attempt_count = int(state.get("persisted_attempt_count", 0))
    events = state.get("events", [])
    attempts = state.get("attempts", [])
    append_event_log(state, start_index=persisted_event_count)
    append_attempt_log(state, start_index=persisted_attempt_count)
    persist_operator_messages(state)
    persist_raw_logs(state)
    persist_patch_proposal(state)
    latest_state = persist_state_snapshot(state)

    return {
        "run_root": str(run_root),
        "latest_persistence_path": str(latest_state),
        "persisted_event_count": len(events),
        "persisted_attempt_count": len(attempts),
    }
