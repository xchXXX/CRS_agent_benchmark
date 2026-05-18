from __future__ import annotations

import re
from typing import Any


_INT_FIELD_PATTERN = re.compile(r"(^|;\s*)(?P<key>[a-z_]+)=(?P<value>-?\d+)")
_TEXT_FIELD_PATTERN = re.compile(r"(^|;\s*)(?P<key>[a-z_]+)=(?P<value>[^;]+)")
STOP_VALID_CODES = {"OPTION_SPACE_CONFLICT", "INSUFFICIENT_INFORMATION"}


def _extract_reason_int(reason: str | None, field_name: str) -> int | None:
    text = str(reason or "")
    for match in _INT_FIELD_PATTERN.finditer(text):
        if match.group("key") == field_name:
            try:
                return int(match.group("value"))
            except ValueError:
                return None
    return None


def _extract_reason_text(reason: str | None, field_name: str) -> str | None:
    text = str(reason or "")
    for match in _TEXT_FIELD_PATTERN.finditer(text):
        if match.group("key") == field_name:
            value = str(match.group("value") or "").strip()
            return value or None
    return None


def _sanitize_option_snapshot(options: list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(options, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        sanitized.append(
            {
                "key": str(item.get("key") or "").strip() or None,
                "label": str(item.get("label") or "").strip() or None,
                "description": str(item.get("description") or "").strip() or None,
            }
        )
    return sanitized


def _summarize_response_body(turn) -> str | None:
    body = turn.response_body
    if not isinstance(body, dict):
        return None
    if turn.response_type == "ask_user":
        question = str((body.get("ask_user") or {}).get("question") or "").strip()
        if question:
            return question
    content = body.get("content")
    if isinstance(content, dict):
        for field_name in ("summary", "message", "query"):
            value = content.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(content, str) and content.strip():
        return content.strip()
    summary = str(body.get("business") or body.get("type") or "").strip()
    return summary or None


def _build_decision_trace(task, result) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = [
        {
            "trace_kind": "initial_user_message",
            "content": task.initial_user_message or task.question_text,
        }
    ]
    for turn in result.workflow.turns:
        trace.append(
            {
                "trace_kind": "turn",
                "turn_index": turn.turn_index,
                "request_kind": turn.request_kind,
                "response_type": turn.response_type,
                "ask_user_question": turn.ask_user_question,
                "visible_options": _sanitize_option_snapshot(turn.clarify_options_snapshot),
                "user_decision_kind": turn.user_decision_kind,
                "selected_option_key": turn.selected_option_key,
                "selected_option_label": turn.selected_option_label,
                "decision_reason": turn.user_decision_reason,
                "stop_reason_code": turn.user_stop_reason_code,
                "decision_evidence": dict(turn.user_decision_evidence),
                "response_summary": _summarize_response_body(turn),
                "capability_gap": turn.capability_gap,
                "stop_reason": turn.stop_reason,
            }
        )
    return trace


def _count_corrections(result) -> int:
    return 0


def _count_ambiguous_turns(result) -> int:
    return 0


def _infer_failure_reason(task, result) -> str | None:
    if result.metrics.recall_hit:
        return None

    if result.workflow.stopped_by_user_simulation:
        stop_reason_code = next(
            (
                turn.user_stop_reason_code
                for turn in result.workflow.turns
                if turn.user_decision_kind == "stop" and turn.user_stop_reason_code
            ),
            None,
        )
        if stop_reason_code == "OPTION_SPACE_CONFLICT":
            return "system_clarification_failure"
        if stop_reason_code == "INSUFFICIENT_INFORMATION":
            return "simulation_valid_stop"
        return "user_simulation_stop"

    blocking_failures = set(result.validation.blocking_failures or [])
    if result.workflow.capability_gaps or "CAPABILITY_GAP_PRESENT" in blocking_failures:
        return "protocol_capability_gap"
    if "ASK_USER_ROUNDS_INSUFFICIENT" in blocking_failures:
        return "insufficient_clarification"

    if result.workflow.ask_user_rounds > 0:
        return "insufficient_clarification"
    return "target_miss"


def build_trace_analysis(task, result) -> dict[str, Any]:
    user_stop_reason_code = next(
        (
            turn.user_stop_reason_code
            for turn in result.workflow.turns
            if turn.user_decision_kind == "stop" and turn.user_stop_reason_code
        ),
        None,
    )
    stopped_by_user_simulation = bool(result.workflow.stopped_by_user_simulation)
    return {
        "final_hit": bool(result.metrics.recall_hit),
        "turn_count": len(result.workflow.turns),
        "decision_trace": _build_decision_trace(task, result),
        "correction_count": _count_corrections(result),
        "ambiguous_turn_count": _count_ambiguous_turns(result),
        "stop_reason": result.workflow.stop_reason,
        "failure_reason": _infer_failure_reason(task, result),
        "stopped_by_user_simulation": stopped_by_user_simulation,
        "simulation_stop_count": int(result.workflow.simulation_stop_count),
        "simulation_valid_stop": (
            True if stopped_by_user_simulation and user_stop_reason_code in STOP_VALID_CODES else None
        ),
        "user_stop_reason_code": user_stop_reason_code,
    }
