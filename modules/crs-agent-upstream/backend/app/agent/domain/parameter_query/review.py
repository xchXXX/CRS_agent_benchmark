"""Deterministic review helpers for parameter-query execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agent.context.models import CaseContext
from app.agent.domain.parameter_query.normalizer import (
    extract_pin_token,
    extract_requested_field,
    normalize_free_text_hint,
    normalize_text,
    remove_known_terms,
)


SOURCE_TOKEN_PATTERN = re.compile(r"\b(?:ecu|[a-z]{2,}\d[a-z0-9]*)\b", re.IGNORECASE)
TARGET_STOP_TERMS = tuple(
    normalize_text(item)
    for item in (
        "ecu",
        "引脚",
        "针脚",
        "脚位",
        "脚号",
        "哪个针脚",
        "在哪个针脚",
        "几号脚",
        "作用",
        "定义",
        "什么意思",
        "是什么",
        "多少",
        "是多少",
        "开路电压",
        "静态电压",
        "怠速电压",
        "电压",
        "备注",
        "查询",
        "查一下",
        "帮我",
        "请问",
    )
    if normalize_text(item)
)


@dataclass(frozen=True)
class ParameterQueryToolReviewResult:
    blocked: bool
    envelope: dict[str, Any] | None = None
    reason: str | None = None


def review_parameter_query_execution(
    *,
    case_context: CaseContext | None,
    runtime_tool_history: list[dict[str, Any]] | None,
    query: str,
    selection_payload: dict[str, Any] | None,
) -> ParameterQueryToolReviewResult:
    current_signature = _build_query_signature(
        case_context=case_context,
        query=query,
        selection_payload=selection_payload,
    )
    if current_signature is None:
        return ParameterQueryToolReviewResult(blocked=False)

    for item in reversed(runtime_tool_history or []):
        if item.get("tool_name") != "query_parameters":
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        previous_signature = _build_query_signature(
            case_context=case_context,
            query=str((item.get("args") or {}).get("query") or ""),
            selection_payload=(item.get("args") or {}).get("selection_payload"),
        )
        if previous_signature != current_signature:
            continue

        status = str(result.get("status") or "").strip().lower()
        data = result.get("data") or {}
        if status == "need_clarify":
            return ParameterQueryToolReviewResult(
                blocked=True,
                envelope=result,
                reason="repeat_need_clarify_query",
            )
        if status == "ok" and data.get("matched") is True:
            return ParameterQueryToolReviewResult(
                blocked=True,
                envelope=result,
                reason="repeat_existing_match",
            )
        if status == "ok" and data.get("matched") is False:
            return ParameterQueryToolReviewResult(
                blocked=True,
                envelope=result,
                reason="repeat_no_match_query",
            )
    return ParameterQueryToolReviewResult(blocked=False)


def _build_query_signature(
    *,
    case_context: CaseContext | None,
    query: str,
    selection_payload: dict[str, Any] | None,
) -> tuple[str, str, str, str] | None:
    filters = dict((selection_payload or {}).get("filters") or {})
    requested_field = _resolve_requested_field(query=query, filters=filters)
    source_key = _resolve_source_key(case_context=case_context, query=query, filters=filters)
    target_key = _resolve_target_key(case_context=case_context, query=query)
    row_key = str(filters.get("param_row_id") or "").strip()
    if not any((source_key, target_key, row_key)):
        return None
    return (
        source_key or "__source__",
        row_key or "__row__",
        requested_field or "__field__",
        target_key or "__target__",
    )


def _resolve_requested_field(*, query: str, filters: dict[str, Any]) -> str | None:
    forced_field = str(filters.get("param_field") or "").strip()
    if forced_field:
        return forced_field
    extracted = extract_requested_field(query)
    if extracted:
        return extracted
    if any(token in query for token in ("哪个针脚", "在哪个针脚", "几号脚", "脚位")):
        return "ecu_pin_no"
    if any(token in query for token in ("作用", "定义", "什么意思")):
        return "pin_definition"
    return None


def _resolve_source_key(
    *,
    case_context: CaseContext | None,
    query: str,
    filters: dict[str, Any],
) -> str | None:
    source_id = str(filters.get("param_source_id") or "").strip()
    if source_id:
        return f"id:{source_id}"

    ecu_model = ""
    if case_context is not None:
        ecu_model = str(case_context.slots.ecu_model or "").strip()
        if case_context.slots.parameter_source_id:
            return f"id:{case_context.slots.parameter_source_id}"
    if ecu_model:
        normalized_ecu = normalize_text(ecu_model)
        if normalized_ecu and normalized_ecu in normalize_text(query):
            return f"ecu:{normalized_ecu}"

    tokens = [normalize_text(match.group(0)) for match in SOURCE_TOKEN_PATTERN.finditer(query)]
    pin_token = normalize_text(extract_pin_token(query) or "")
    for token in tokens:
        if not token or token == pin_token:
            continue
        return f"ecu:{token}"
    return None


def _resolve_target_key(
    *,
    case_context: CaseContext | None,
    query: str,
) -> str | None:
    pin_token = extract_pin_token(query)
    if pin_token:
        return f"pin:{pin_token}"

    known_terms = list(TARGET_STOP_TERMS)
    if case_context is not None:
        for value in (
            case_context.slots.brand,
            case_context.slots.series,
            case_context.slots.model,
            case_context.slots.ecu_model,
        ):
            normalized = normalize_text(value)
            if normalized:
                known_terms.append(normalized)

    for match in SOURCE_TOKEN_PATTERN.finditer(query):
        normalized = normalize_text(match.group(0))
        if normalized:
            known_terms.append(normalized)

    stripped = remove_known_terms(normalize_text(query), known_terms)
    target_hint = normalize_free_text_hint(stripped)
    if not target_hint:
        return None
    return f"hint:{target_hint}"
