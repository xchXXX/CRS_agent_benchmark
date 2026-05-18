"""Local persistence for coding-engine runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.coding_engine.config import CODING_RUNS_DIR
from app.coding_engine.state import CodingEngineState


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def ensure_run_root(run_id: str, run_root: str | None = None) -> Path:
    root = Path(run_root).expanduser().resolve() if run_root else (CODING_RUNS_DIR / run_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _public_state_snapshot(state: CodingEngineState) -> dict[str, Any]:
    snapshot = dict(state)
    if "raw_logs" in snapshot:
        snapshot["raw_logs_truncated"] = len(snapshot["raw_logs"])
        snapshot.pop("raw_logs", None)
    snapshot.pop("persisted_event_count", None)
    snapshot.pop("persisted_attempt_count", None)
    return snapshot


def persist_state_snapshot(state: CodingEngineState) -> Path:
    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    latest_path = run_root / "state.latest.json"
    latest_path.write_text(
        json.dumps(_public_state_snapshot(state), ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return latest_path


def append_event_log(state: CodingEngineState, *, start_index: int = 0) -> Path:
    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    events_path = run_root / "events.jsonl"
    new_events = state.get("events", [])[start_index:]
    if not new_events:
        return events_path
    with events_path.open("a", encoding="utf-8") as handle:
        for event in new_events:
            handle.write(json.dumps(event, ensure_ascii=False, default=_json_default))
            handle.write("\n")
    return events_path


def append_attempt_log(state: CodingEngineState, *, start_index: int = 0) -> Path:
    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    attempts_path = run_root / "attempts.jsonl"
    attempts = state.get("attempts", [])[start_index:]
    if not attempts:
        return attempts_path
    with attempts_path.open("a", encoding="utf-8") as handle:
        for attempt in attempts:
            handle.write(json.dumps(attempt, ensure_ascii=False, default=_json_default))
            handle.write("\n")
    return attempts_path


def persist_raw_logs(state: CodingEngineState) -> Path | None:
    raw_logs = state.get("raw_logs", "")
    if not raw_logs:
        return None
    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    iteration = int(state.get("iteration", 0))
    logs_path = run_root / f"harness.iteration-{iteration:03d}.log"
    logs_path.write_text(raw_logs, encoding="utf-8")
    return logs_path


def persist_patch_proposal(state: CodingEngineState) -> Path | None:
    proposed_patch = state.get("proposed_patch", "").strip()
    if not proposed_patch:
        return None
    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    iteration = int(state.get("iteration", 0))
    patch_path = run_root / f"proposal.iteration-{iteration:03d}.md"
    patch_path.write_text(proposed_patch, encoding="utf-8")
    return patch_path


def persist_operator_messages(state: CodingEngineState) -> Path | None:
    operator_messages = state.get("operator_messages", [])
    if not operator_messages:
        return None
    run_root = ensure_run_root(state["run_id"], state.get("run_root"))
    operator_path = run_root / "operator_messages.json"
    operator_path.write_text(
        json.dumps(operator_messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return operator_path
