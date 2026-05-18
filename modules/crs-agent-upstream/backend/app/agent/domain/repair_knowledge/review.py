"""Deterministic review rules for repair-knowledge gate decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.agent.adapters.repair_knowledge_followup_adapter import RepairKnowledgeFollowupAdapter
from app.agent.domain.repair_knowledge.rendering import (
    RepairAnswerDepth,
    RepairAnswerFrame,
    build_repair_render_context,
    default_repair_render_plan,
)
from app.agent.models.ask_user import AskUserInputType, AskUserQuestion


FAULT_CODE_PATTERN = re.compile(r"\b[PBCU][0-9A-F]{4}\b", re.IGNORECASE)
ECU_HINT_PATTERN = re.compile(r"\b(?:ECU|EDC\d+[A-Z0-9]*)\b", re.IGNORECASE)
TEMPERATURE_PATTERN = re.compile(r"-?\d+\s*(?:°?C|℃|度)", re.IGNORECASE)
WORKING_CONDITION_HINTS = ("急加速", "爬坡", "重载", "高速", "怠速", "冷车", "热车", "起步")
FAULT_PHENOMENON_HINTS = (
    "动力不足",
    "冒黑烟",
    "冒白烟",
    "抖动",
    "熄火",
    "无力",
    "报码",
    "故障",
    "难启动",
    "难起动",
    "启动困难",
    "打不着火",
    "启动时间长",
    "启动后熄火",
)


@dataclass(frozen=True)
class RepairAnswerReviewDecision:
    allow_ready: bool
    force_ask_user: bool
    missing_field_keys: list[str]
    ask_user: AskUserQuestion | None
    frame: str = ""
    answer_depth: str = ""
    blocking_reasons: list[str] | None = None


def review_repair_answer_gate(
    *,
    query: str,
    loaded_context: dict[str, Any] | None,
    no_gain_streak: int = 0,
) -> RepairAnswerReviewDecision:
    loaded = isinstance(loaded_context, dict) and loaded_context.get("loaded")
    render_context = build_repair_render_context(
        query=query,
        loaded_context=loaded_context if isinstance(loaded_context, dict) else None,
    )
    render_plan = default_repair_render_plan(render_context)

    if not loaded:
        if not RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query(query):
            return RepairAnswerReviewDecision(
                allow_ready=False,
                force_ask_user=False,
                missing_field_keys=[],
                ask_user=None,
                frame=render_plan.frame.value,
                answer_depth=render_plan.answer_depth.value,
                blocking_reasons=["not_repair_diagnosis_query"],
            )

        field_groups = _build_query_field_groups(query)
        ask_user = _build_query_ask_user(query=query, field_groups=field_groups)
        return _finalize_review_decision(
            query=query,
            render_plan=render_plan,
            field_groups=field_groups,
            ask_user=ask_user,
            has_loaded_sources=False,
            no_gain_streak=no_gain_streak,
        )

    answer_text = str(loaded_context.get("llm_context") or "").strip()
    if not answer_text:
        answer_text = "\n".join(str(item.get("content") or "") for item in (loaded_context.get("entries") or []))

    ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
        query=query,
        loaded_context=loaded_context,
        answer_text=answer_text,
    )
    field_groups = list((ask_user.context or {}).get("field_groups") or [])
    return _finalize_review_decision(
        query=query,
        render_plan=render_plan,
        field_groups=field_groups,
        ask_user=ask_user,
        has_loaded_sources=bool(loaded_context.get("source_refs")),
        no_gain_streak=no_gain_streak,
    )


async def review_repair_answer_gate_async(
    *,
    query: str,
    loaded_context: dict[str, Any] | None,
    no_gain_streak: int = 0,
) -> RepairAnswerReviewDecision:
    loaded = isinstance(loaded_context, dict) and loaded_context.get("loaded")
    render_context = build_repair_render_context(
        query=query,
        loaded_context=loaded_context if isinstance(loaded_context, dict) else None,
    )
    render_plan = default_repair_render_plan(render_context)

    if not loaded:
        if not RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query(query):
            return RepairAnswerReviewDecision(
                allow_ready=False,
                force_ask_user=False,
                missing_field_keys=[],
                ask_user=None,
                frame=render_plan.frame.value,
                answer_depth=render_plan.answer_depth.value,
                blocking_reasons=["not_repair_diagnosis_query"],
            )

        field_groups = await _build_query_field_groups_async(query)
        ask_user = _build_query_ask_user(query=query, field_groups=field_groups)
        return _finalize_review_decision(
            query=query,
            render_plan=render_plan,
            field_groups=field_groups,
            ask_user=ask_user,
            has_loaded_sources=False,
            no_gain_streak=no_gain_streak,
        )

    answer_text = str(loaded_context.get("llm_context") or "").strip()
    if not answer_text:
        answer_text = "\n".join(str(item.get("content") or "") for item in (loaded_context.get("entries") or []))

    ask_user = await RepairKnowledgeFollowupAdapter.build_ask_user_question_async(
        query=query,
        loaded_context=loaded_context,
        answer_text=answer_text,
    )
    field_groups = list((ask_user.context or {}).get("field_groups") or [])
    return _finalize_review_decision(
        query=query,
        render_plan=render_plan,
        field_groups=field_groups,
        ask_user=ask_user,
        has_loaded_sources=bool(loaded_context.get("source_refs")),
        no_gain_streak=no_gain_streak,
    )


def _evaluate_gate_readiness(
    *,
    frame: RepairAnswerFrame,
    depth: RepairAnswerDepth,
    has_loaded_sources: bool,
    hard_missing: list[dict[str, Any]],
    strong_missing: list[dict[str, Any]],
    missing_field_keys: list[str],
    no_gain_streak: int,
) -> tuple[bool, bool, list[str]]:
    reasons: list[str] = []

    if frame in {
        RepairAnswerFrame.LOCATION_IDENTIFICATION,
        RepairAnswerFrame.PRINCIPLE_EXPLANATION,
        RepairAnswerFrame.OPERATION_GUIDE,
    }:
        allow_ready = True
        force_ask_user = False
        if frame == RepairAnswerFrame.OPERATION_GUIDE and no_gain_streak > 0 and missing_field_keys:
            reasons.append("operation_missing_context")
        return allow_ready, force_ask_user, reasons

    if frame == RepairAnswerFrame.SPEC_ANSWER:
        allow_ready = True
        force_ask_user = False
        if no_gain_streak > 0 and missing_field_keys:
            reasons.append("spec_missing_context")
        return allow_ready, force_ask_user, reasons

    if not has_loaded_sources:
        if hard_missing:
            reasons.append("diagnosis_missing_hard_fields")
        if len(strong_missing) >= 2:
            reasons.append("diagnosis_missing_too_many_strong_fields")
        if no_gain_streak > 0 and missing_field_keys:
            reasons.append("diagnosis_stalled_without_sources")
        force_ask_user = bool(reasons)
        allow_ready = not force_ask_user
        return allow_ready, force_ask_user, reasons

    strong_limit = 0 if depth == RepairAnswerDepth.PLAYBOOK else 1
    if hard_missing:
        reasons.append("diagnosis_missing_hard_fields")
    if len(strong_missing) > strong_limit:
        reasons.append("diagnosis_missing_too_many_strong_fields")
    if no_gain_streak > 0 and missing_field_keys:
        reasons.append("diagnosis_stalled_with_missing_fields")

    force_ask_user = bool(reasons)
    allow_ready = not force_ask_user
    return allow_ready, force_ask_user, reasons


def _finalize_review_decision(
    *,
    query: str,
    render_plan: Any,
    field_groups: list[dict[str, Any]],
    ask_user: AskUserQuestion,
    has_loaded_sources: bool,
    no_gain_streak: int,
) -> RepairAnswerReviewDecision:
    missing_groups = [group for group in field_groups if not _query_satisfies_group(query, group)]
    missing_field_keys = [str(group.get("key") or "") for group in missing_groups if str(group.get("key") or "")]
    hard_missing = [
        group
        for group in missing_groups
        if str(group.get("required_level") or "") == "hard"
    ]
    strong_missing = [
        group
        for group in missing_groups
        if str(group.get("required_level") or "") == "strong"
    ]

    allow_ready, force_ask_user, blocking_reasons = _evaluate_gate_readiness(
        frame=render_plan.frame,
        depth=render_plan.answer_depth,
        has_loaded_sources=has_loaded_sources,
        hard_missing=hard_missing,
        strong_missing=strong_missing,
        missing_field_keys=missing_field_keys,
        no_gain_streak=no_gain_streak,
    )

    if force_ask_user and missing_groups:
        context = dict(ask_user.context or {})
        context["field_groups"] = missing_groups
        context["ask_reason"] = RepairKnowledgeFollowupAdapter._resolve_ask_reason(missing_groups)
        ask_user = ask_user.model_copy(update={"context": context})

    return RepairAnswerReviewDecision(
        allow_ready=allow_ready,
        force_ask_user=force_ask_user,
        missing_field_keys=missing_field_keys,
        ask_user=ask_user if force_ask_user else None,
        frame=render_plan.frame.value,
        answer_depth=render_plan.answer_depth.value,
        blocking_reasons=blocking_reasons,
    )


def _build_query_field_groups(query: str) -> list[dict[str, Any]]:
    normalized_query = RepairKnowledgeFollowupAdapter.normalize_query_text(query)
    lowered = normalized_query.lower()

    llm_groups, llm_ask_reason = RepairKnowledgeFollowupAdapter._build_field_groups_from_llm_plan(
        query=normalized_query,
        loaded_context=None,
        answer_text=normalized_query,
    )
    if llm_groups:
        return RepairKnowledgeFollowupAdapter._apply_llm_ask_reason(llm_groups, llm_ask_reason)

    if RepairKnowledgeFollowupAdapter.is_starting_issue_query(normalized_query):
        specs = [
            ("fault_phenomenon", "起动现象描述"),
            ("working_condition", "故障发生背景"),
            ("fault_codes", "故障码情况"),
            ("ecu_or_system", "车型/系统信息"),
        ]
    elif any(hint in lowered for hint in RepairKnowledgeFollowupAdapter.COMMUNICATION_HINTS):
        specs = [
            ("ecu_or_system", "涉及的ECU或系统"),
            ("fault_phenomenon", "故障现象"),
            ("fault_codes", "故障码情况"),
        ]
    elif any(hint in normalized_query for hint in RepairKnowledgeFollowupAdapter.POWER_LOSS_HINTS):
        specs = [
            ("fault_phenomenon", "当前动力不足表现"),
            ("working_condition", "出现动力不足的工况"),
            ("fault_codes", "当前故障码情况"),
            ("data_evidence", "已掌握的关键数据"),
        ]
    else:
        specs = RepairKnowledgeFollowupAdapter._fallback_group_specs(query=normalized_query)

    return [
        RepairKnowledgeFollowupAdapter._build_group_for_key(
            key=key,
            label=label,
            query=normalized_query,
            loaded_context=None,
        )
        for key, label in specs
    ]


async def _build_query_field_groups_async(query: str) -> list[dict[str, Any]]:
    normalized_query = RepairKnowledgeFollowupAdapter.normalize_query_text(query)
    lowered = normalized_query.lower()

    llm_groups, llm_ask_reason = await RepairKnowledgeFollowupAdapter._build_field_groups_from_llm_plan_async(
        query=normalized_query,
        loaded_context=None,
        answer_text=normalized_query,
    )
    if llm_groups:
        return RepairKnowledgeFollowupAdapter._apply_llm_ask_reason(llm_groups, llm_ask_reason)

    if RepairKnowledgeFollowupAdapter.is_starting_issue_query(normalized_query):
        specs = [
            ("fault_phenomenon", "起动现象描述"),
            ("working_condition", "故障发生背景"),
            ("fault_codes", "故障码情况"),
            ("ecu_or_system", "车型/系统信息"),
        ]
    elif any(hint in lowered for hint in RepairKnowledgeFollowupAdapter.COMMUNICATION_HINTS):
        specs = [
            ("ecu_or_system", "涉及的ECU或系统"),
            ("fault_phenomenon", "故障现象"),
            ("fault_codes", "故障码情况"),
        ]
    elif any(hint in normalized_query for hint in RepairKnowledgeFollowupAdapter.POWER_LOSS_HINTS):
        specs = [
            ("fault_phenomenon", "当前动力不足表现"),
            ("working_condition", "出现动力不足的工况"),
            ("fault_codes", "当前故障码情况"),
            ("data_evidence", "已掌握的关键数据"),
        ]
    else:
        specs = RepairKnowledgeFollowupAdapter._fallback_group_specs(query=normalized_query)

    groups: list[dict[str, Any]] = []
    for key, label in specs:
        groups.append(
            await RepairKnowledgeFollowupAdapter._build_group_for_key_async(
                key=key,
                label=label,
                query=normalized_query,
                loaded_context=None,
            )
        )
    return groups


def _build_query_ask_user(*, query: str, field_groups: list[dict[str, Any]]) -> AskUserQuestion:
    field_groups_source = "rule"
    if any(str(group.get("option_source") or "").strip() == "llm_predicted" for group in field_groups):
        field_groups_source = "llm_plan"
    return AskUserQuestion(
        tool_call_id=f"repair_review_followup_{uuid4().hex}",
        question="请先补充以下关键信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        input_hint="优先点选，若没有合适选项再手动补充",
        context={
            "scene": "repair_knowledge_followup",
            "card_type": "repair_followup",
            "ask_mode": "batch_once",
            "query": query,
            "repair_knowledge_query": query,
            "ask_reason": RepairKnowledgeFollowupAdapter._resolve_ask_reason(field_groups),
            "field_groups": field_groups,
            "field_groups_source": field_groups_source,
            "quick_actions": [],
        },
    )


def _query_satisfies_group(query: str, group: dict[str, Any]) -> bool:
    field_key = str(group.get("key") or "")
    label = str(group.get("label") or "")
    normalized_query = RepairKnowledgeFollowupAdapter.normalize_query_text(query)
    lowered = normalized_query.lower()
    is_communication = any(hint in lowered for hint in RepairKnowledgeFollowupAdapter.COMMUNICATION_HINTS)

    if field_key == "fault_codes":
        if FAULT_CODE_PATTERN.search(normalized_query):
            return True
        if "暂未读取到具体报码" in normalized_query or "当前无报码" in normalized_query:
            return True
        return False
    if field_key == "data_evidence":
        return (
            "数据流" in normalized_query
            or any(hint in normalized_query for hint in RepairKnowledgeFollowupAdapter.DATA_STREAM_HINTS)
            or "csv" in lowered
        )
    if field_key == "ecu_or_system":
        if "品牌" in label or "发动机型号" in label:
            return bool(
                ECU_HINT_PATTERN.search(normalized_query)
                or "发动机" in normalized_query
                or "车型" in normalized_query
                or "品牌" in normalized_query
            )
        return bool(
            ECU_HINT_PATTERN.search(normalized_query)
            or "系统" in normalized_query
            or "型号" in normalized_query
            or "机型" in normalized_query
            or "吨位" in normalized_query
            or "挖机" in normalized_query
            or "挖掘机" in normalized_query
            or "设备" in normalized_query
        )
    if field_key == "working_condition":
        if "温度" in label:
            return bool(TEMPERATURE_PATTERN.search(normalized_query)) or any(
                hint in normalized_query for hint in ("零下", "低温", "摄氏", "℃", "度")
            )
        return any(hint in normalized_query for hint in WORKING_CONDITION_HINTS)
    if field_key == "fault_phenomenon":
        if is_communication:
            return any(
                hint in normalized_query
                for hint in (
                    "通讯中断",
                    "多个模块离线",
                    "多个模块同时报码",
                    "仪表报码",
                    "整车动力受限",
                    "限速",
                    "限扭",
                    "车辆限扭",
                    "无法启动",
                    "熄火",
                    "报码偶发",
                    "报码频发",
                )
            )
        return any(hint in normalized_query for hint in FAULT_PHENOMENON_HINTS)
    if field_key == "repair_history":
        return any(hint in normalized_query for hint in ("维修", "更换", "保养", "处理过", "修过"))
    return False
