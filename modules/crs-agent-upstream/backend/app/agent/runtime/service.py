"""Agent Loop service implementation."""

import asyncio
from datetime import datetime, timezone
import json
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence
from uuid import uuid4

from genai_prices import calc_price

from app.agent.ask_user_v2 import (
    attach_form_to_ask_user,
    build_single_field_form,
    normalize_ask_user_question_v2,
    normalize_ask_user_question_v2_async,
)
from app.agent.context import CaseContextManager, CaseContextPromptBuilder, LoopGuard, LoopGuardExceededError
from app.agent.adapters.doc_search_response_adapter import DOC_SEARCH_DEFERRED_TOOL_NAME, DocSearchResponseAdapter
from app.agent.adapters.legacy_doc_search_adapter import LegacyDocSearchAdapter
from app.agent.domain.circuit_body_search import resolve_circuit_body_keyword
from app.agent.adapters.repair_knowledge_followup_adapter import RepairKnowledgeFollowupAdapter
from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner
from app.agent.domain.parameter_query.response_adapter import (
    PARAM_QUERY_DEFERRED_TOOL_NAME,
    ParameterQueryResponseAdapter,
)
from app.agent.domain.repair_knowledge.rendering import (
    RepairAnswerFrame,
    RepairRenderContext,
    RepairRenderPlan,
    build_repair_render_context,
    build_repair_render_fallback_content,
    default_repair_render_plan,
    get_repair_render_strategy,
    review_repair_rendered_answer,
    validate_repair_render_plan,
)
from app.agent.domain.repair_knowledge.review import review_repair_answer_gate, review_repair_answer_gate_async
from app.agent.memory.deferred_store import DeferredState
from app.agent.models.ask_user import AskUserInputType, AskUserOption, AskUserQuestion
from app.agent.models.events import AgentEventType, AgentRuntimeEvent
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.intent_router import IntentDecision, RequestIntentRouter, RoutedIntent
from app.core.config import settings
from app.schemas.chat import AskUserAnswer, ChatRequest, ChatResponse, ClarifyOption


@dataclass
class ActiveStreamState:
    message_history: Sequence[Any] | None
    user_prompt: str | None


@dataclass(frozen=True)
class DocSearchWorkflowRunState:
    query: str
    original_query: str | None = None
    clarify_round: int = 0
    deferred_state: DeferredState | None = None


@dataclass(frozen=True)
class ParameterQueryWorkflowRunState:
    query: str
    deferred_state: DeferredState | None = None


@dataclass(frozen=True)
class DocSearchExecutedQuery:
    query: str
    confidence: float


@dataclass(frozen=True)
class DocSearchPlannedSearchResult:
    envelope: dict[str, Any]
    executed_queries: tuple[DocSearchExecutedQuery, ...]
    primary_query: str
    rationale: str = ""
    body_keyword: str = ""


@dataclass(frozen=True)
class RepairAnswerGateReadyState:
    message_history: Sequence[Any]
    query: str
    run_messages: Sequence[Any]


@dataclass(frozen=True)
class RepairFollowupSummaryState:
    summary_text: str
    field_values: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class RepairFollowupQueryState:
    original_query: str
    evidence_query: str


@dataclass(frozen=True)
class GuardConvergenceResult:
    response: ChatResponse
    mode: str


@dataclass(frozen=True)
class RepairRenderRuntimeState:
    message_history: Sequence[Any]
    user_prompt: str
    run_messages: Sequence[Any]
    plan: RepairRenderPlan
    context: RepairRenderContext


@dataclass(frozen=True)
class LLMRunObservability:
    model_name: str | None = None
    provider_name: str | None = None
    provider_url: str | None = None
    provider_response_id: str | None = None
    finish_reason: str | None = None
    run_id: str | None = None
    response_timestamp: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    input_audio_tokens: int = 0
    output_audio_tokens: int = 0
    reasoning_tokens: int = 0
    usage_details: dict[str, int] | None = None
    request_count: int = 0
    tool_call_count: int = 0
    llm_elapsed_ms: int | None = None
    first_response_ms: int | None = None
    estimated_cost_usd: float | None = None
    cost_error: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        usage = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "input_audio_tokens": self.input_audio_tokens,
            "output_audio_tokens": self.output_audio_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "request_count": self.request_count,
            "tool_call_count": self.tool_call_count,
            "details": dict(self.usage_details or {}),
        }
        metadata = {
            "model_name": self.model_name,
            "provider_name": self.provider_name,
            "provider_url": self.provider_url,
            "provider_response_id": self.provider_response_id,
            "finish_reason": self.finish_reason,
            "run_id": self.run_id,
            "response_timestamp": self.response_timestamp,
            "usage": usage,
            "llm_elapsed_ms": self.llm_elapsed_ms,
            "first_response_ms": self.first_response_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
        }
        if self.cost_error:
            metadata["cost_error"] = self.cost_error
        return metadata

    def to_trace_payload(self) -> dict[str, Any]:
        payload = {
            "model_name": self.model_name,
            "provider_name": self.provider_name,
            "finish_reason": self.finish_reason,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "request_count": self.request_count,
                "tool_call_count": self.tool_call_count,
            },
            "llm_elapsed_ms": self.llm_elapsed_ms,
            "first_response_ms": self.first_response_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
        }
        if self.cost_error:
            payload["cost_error"] = self.cost_error
        return payload


class AgentLoopService:
    _DOC_SEARCH_IMAGE_CODE_RE = re.compile(r"\b[A-Z]{1,6}[A-Z0-9_-]{2,}\b")
    _DOC_SEARCH_NUMERIC_CODE_RE = re.compile(r"\b\d{5,}\b")
    _DOC_SEARCH_CHINESE_HINT_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
    _DOC_SEARCH_COMPANY_SUFFIX_RE = re.compile(r"(汽车电子有限公司|电子有限公司|有限公司|汽车电子|电子)$")
    _DOC_SEARCH_CITY_PREFIX_RE = re.compile(
        r"^(苏州|上海|深圳|无锡|南京|常州|广州|北京|重庆|武汉|杭州|宁波|郑州|成都|西安|青岛|天津)"
    )
    _DOC_SEARCH_QUERY_SLASH_RE = re.compile(r"\s*[／/｜|]+\s*")
    _DOC_SEARCH_QUERY_SPACE_RE = re.compile(r"\s+")
    _DOC_SEARCH_QUERY_EDGE_RE = re.compile(r"^[\s,，。；;：:/|_+-]+|[\s,，。；;：:/|_+-]+$")
    _DOC_SEARCH_GENERIC_HINT_WORDS = {
        "发动机",
        "控制器",
        "控制单元",
        "电脑板",
        "计量单元",
        "单元",
        "系统",
        "故障码",
        "报码",
        "资料",
        "电路图",
        "针脚定义",
        "铭牌",
        "标签",
        "线束",
        "插头",
        "接口",
        "传感器",
        "继电器",
        "保险盒",
    }
    _DOC_SEARCH_DOC_TYPE_HINTS = (
        "电脑板针脚定义",
        "针脚定义",
        "ECU电路图",
        "发动机电路图",
        "电路图",
        "线束图",
        "原理图",
        "维修手册",
        "资料",
    )
    _DOC_SEARCH_CODE_PRIORITY_HINTS = ("ECUA", "ECU", "EDC", "DCU", "MDD", "ME")
    """Main runtime service for the new backend."""

    _INTENT_CONTEXT_KEY = "__resolved_request_intent"
    _RESUME_BUSINESS_CONTEXT_KEY = "__resume_business"
    _IMAGE_EVIDENCE_CONTEXT_KEYS = ("image_evidence", "image_evidences")

    def __init__(self, deps: AgentRuntimeDeps, factory: AgentFactory | None = None):
        self._deps = deps
        self._factory = factory or AgentFactory()
        self._status = self._factory.get_status()
        self._active_streams: dict[str, ActiveStreamState] = {}

    @staticmethod
    def _mask_token(token: str | None, head: int = 20) -> str | None:
        if not token:
            return None
        if len(token) <= head:
            return token
        return f"{token[:head]}..."

    def _request_trace_payload(self, active_deps: AgentRuntimeDeps, request_id: str) -> dict[str, Any]:
        payload = {
            "request_id": request_id,
            "user_id": active_deps.user_id,
            "has_app_token": bool(active_deps.app_token),
            "app_token": self._mask_token(active_deps.app_token),
        }
        llm_observability = getattr(active_deps, "llm_observability", None)
        if isinstance(llm_observability, dict) and llm_observability:
            payload["llm_observability"] = llm_observability
        loop_guard = getattr(active_deps, "loop_guard", None)
        if loop_guard is not None:
            payload["loop_guard_budget"] = loop_guard.snapshot().__dict__
        return payload

    @staticmethod
    def _isoformat_utc(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @staticmethod
    def _clean_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @classmethod
    def _extract_llm_run_observability(
        cls,
        *,
        result: Any,
        llm_started_at: float,
        first_response_at: float | None = None,
    ) -> LLMRunObservability | None:
        usage_reader = getattr(result, "usage", None)
        usage = usage_reader() if callable(usage_reader) else usage_reader
        response = getattr(result, "response", None)
        usage_details = {}
        if usage is not None:
            usage_details = dict(getattr(usage, "details", {}) or {})
        reasoning_tokens = 0
        for key in (
            "reasoning_tokens",
            "output_reasoning_tokens",
            "completion_reasoning_tokens",
        ):
            value = usage_details.get(key)
            if isinstance(value, (int, float)):
                reasoning_tokens = int(value)
                break

        model_name = getattr(response, "model_name", None)
        provider_name = getattr(response, "provider_name", None)
        provider_url = getattr(response, "provider_url", None)
        provider_response_id = getattr(response, "provider_response_id", None)
        finish_reason = getattr(response, "finish_reason", None)
        run_id = getattr(result, "run_id", None) or getattr(response, "run_id", None)
        response_timestamp = cls._isoformat_utc(getattr(response, "timestamp", None))

        estimated_cost_usd: float | None = None
        cost_error: str | None = None
        if usage is not None and model_name:
            try:
                price = calc_price(
                    usage,
                    model_name,
                    provider_id=provider_name,
                    provider_api_url=provider_url if provider_name is None else None,
                )
                total_price = getattr(price, "total_price", None)
                if total_price is not None:
                    estimated_cost_usd = float(total_price)
            except Exception as exc:
                cost_error = str(exc)

        llm_elapsed_ms = max(0, int((time.perf_counter() - llm_started_at) * 1000))
        first_response_ms = None
        if first_response_at is not None:
            first_response_ms = max(0, int((first_response_at - llm_started_at) * 1000))

        has_signal = any(
            (
                model_name,
                provider_name,
                provider_url,
                provider_response_id,
                finish_reason,
                run_id,
                response_timestamp,
                usage is not None,
                estimated_cost_usd is not None,
                cost_error,
            )
        )
        if not has_signal:
            return None

        return LLMRunObservability(
            model_name=cls._clean_optional_string(model_name),
            provider_name=cls._clean_optional_string(provider_name),
            provider_url=cls._clean_optional_string(provider_url),
            provider_response_id=cls._clean_optional_string(provider_response_id),
            finish_reason=cls._clean_optional_string(finish_reason),
            run_id=cls._clean_optional_string(run_id),
            response_timestamp=response_timestamp,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_write_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_tokens", 0) or 0),
            input_audio_tokens=int(getattr(usage, "input_audio_tokens", 0) or 0),
            output_audio_tokens=int(getattr(usage, "output_audio_tokens", 0) or 0),
            reasoning_tokens=reasoning_tokens,
            usage_details={k: int(v) for k, v in usage_details.items() if isinstance(v, (int, float))},
            request_count=int(getattr(usage, "requests", 0) or 0),
            tool_call_count=int(getattr(usage, "tool_calls", 0) or 0),
            llm_elapsed_ms=llm_elapsed_ms,
            first_response_ms=first_response_ms,
            estimated_cost_usd=estimated_cost_usd,
            cost_error=cost_error,
        )

    @staticmethod
    def _merge_llm_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        keys = {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_write_tokens",
            "cache_read_tokens",
            "input_audio_tokens",
            "output_audio_tokens",
            "reasoning_tokens",
            "request_count",
            "tool_call_count",
        }
        merged = {key: int(left.get(key, 0) or 0) + int(right.get(key, 0) or 0) for key in keys}
        detail_counter: dict[str, int] = {}
        for details in (left.get("details"), right.get("details")):
            if not isinstance(details, dict):
                continue
            for key, value in details.items():
                if isinstance(value, (int, float)):
                    detail_counter[str(key)] = detail_counter.get(str(key), 0) + int(value)
        merged["details"] = detail_counter
        return merged

    @classmethod
    def _append_llm_observability_call(
        cls,
        *,
        current: dict[str, Any] | None,
        call: dict[str, Any],
        phase: str | None,
    ) -> dict[str, Any]:
        call_metadata = dict(call)
        normalized_phase = cls._clean_optional_string(phase)
        if normalized_phase:
            call_metadata["phase"] = normalized_phase

        existing_calls = current.get("calls") if isinstance(current, dict) else None
        calls = [dict(item) for item in existing_calls if isinstance(item, dict)] if isinstance(existing_calls, list) else []
        calls.append(call_metadata)

        aggregate_usage: dict[str, Any] = {}
        aggregate_cost = 0.0
        has_cost = False
        aggregate_elapsed_ms = 0
        first_response_ms: int | None = None
        provider_names: list[str] = []
        model_names: list[str] = []
        cost_errors: list[str] = []

        for item in calls:
            usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
            aggregate_usage = cls._merge_llm_usage(aggregate_usage, usage)

            elapsed_ms = item.get("llm_elapsed_ms")
            if isinstance(elapsed_ms, (int, float)):
                aggregate_elapsed_ms += max(0, int(elapsed_ms))

            item_first_response_ms = item.get("first_response_ms")
            if isinstance(item_first_response_ms, (int, float)):
                value = max(0, int(item_first_response_ms))
                first_response_ms = value if first_response_ms is None else min(first_response_ms, value)

            cost = item.get("estimated_cost_usd")
            if isinstance(cost, (int, float)):
                aggregate_cost += float(cost)
                has_cost = True

            model_name = cls._clean_optional_string(item.get("model_name"))
            if model_name and model_name not in model_names:
                model_names.append(model_name)
            provider_name = cls._clean_optional_string(item.get("provider_name"))
            if provider_name and provider_name not in provider_names:
                provider_names.append(provider_name)
            cost_error = cls._clean_optional_string(item.get("cost_error"))
            if cost_error and cost_error not in cost_errors:
                cost_errors.append(cost_error)

        latest_non_empty: dict[str, Any] = {}
        for key in (
            "model_name",
            "provider_name",
            "provider_url",
            "provider_response_id",
            "finish_reason",
            "run_id",
            "response_timestamp",
        ):
            for item in reversed(calls):
                value = cls._clean_optional_string(item.get(key))
                if value is not None:
                    latest_non_empty[key] = value
                    break

        metadata = dict(call_metadata)
        metadata.update(latest_non_empty)
        metadata["calls"] = calls
        metadata["call_count"] = len(calls)
        metadata["aggregate_usage"] = aggregate_usage
        metadata["aggregate_llm_elapsed_ms"] = aggregate_elapsed_ms
        metadata["aggregate_first_response_ms"] = first_response_ms
        metadata["aggregate_estimated_cost_usd"] = aggregate_cost if has_cost else None
        metadata["model_names"] = model_names
        metadata["provider_names"] = provider_names
        if cost_errors:
            metadata["cost_errors"] = cost_errors
        return metadata

    @classmethod
    def _record_llm_run_observability(
        cls,
        *,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        result: Any,
        llm_started_at: float,
        first_response_at: float | None = None,
        phase: str | None = None,
    ) -> LLMRunObservability | None:
        observability = cls._extract_llm_run_observability(
            result=result,
            llm_started_at=llm_started_at,
            first_response_at=first_response_at,
        )
        if observability is None:
            return None
        active_deps.llm_observability = cls._append_llm_observability_call(
            current=getattr(active_deps, "llm_observability", None),
            call=observability.to_metadata(),
            phase=phase,
        )
        tracer = getattr(active_deps, "tracer", None)
        if tracer is not None:
            tracer.trace(
                event_type="agent_loop_llm_run_summary",
                session_id=session_id,
                payload={
                    **observability.to_trace_payload(),
                    "phase": phase,
                    "call_count": active_deps.llm_observability.get("call_count")
                    if isinstance(active_deps.llm_observability, dict)
                    else None,
                },
            )
        return observability

    @staticmethod
    def _merge_response_metadata(
        *,
        base: dict[str, Any] | None = None,
        llm_observability: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(base or {})
        if llm_observability:
            metadata["llm"] = dict(llm_observability)
        if extra:
            metadata.update(extra)
        return metadata

    @classmethod
    def _cached_intent_decision(cls, request: ChatRequest) -> IntentDecision | None:
        context = request.context if isinstance(request.context, dict) else {}
        return IntentDecision.from_payload(context.get(cls._INTENT_CONTEXT_KEY))

    @classmethod
    def _cache_intent_decision(cls, request: ChatRequest, decision: IntentDecision) -> None:
        if not isinstance(request.context, dict):
            request.context = {}
        request.context[cls._INTENT_CONTEXT_KEY] = decision.to_payload()

    @classmethod
    def _extract_request_image_evidence_payloads(cls, request: ChatRequest) -> list[dict[str, Any]]:
        context = request.context if isinstance(request.context, dict) else {}
        payloads: list[dict[str, Any]] = []
        for key in cls._IMAGE_EVIDENCE_CONTEXT_KEYS:
            payloads.extend(cls._coerce_image_evidence_payloads(context.get(key)))

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in payloads:
            evidence_id = str(payload.get("image_evidence_id") or "").strip()
            if not evidence_id:
                evidence_id = json.dumps(payload, sort_keys=True, ensure_ascii=False)[:512]
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            deduped.append(payload)
        return deduped

    @classmethod
    def _coerce_image_evidence_payloads(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, list):
            payloads: list[dict[str, Any]] = []
            for item in value:
                payloads.extend(cls._coerce_image_evidence_payloads(item))
            return payloads
        if not isinstance(value, dict):
            return []

        if isinstance(value.get("evidence"), dict):
            return cls._coerce_image_evidence_payloads(value.get("evidence"))
        if isinstance(value.get("image_evidence"), dict):
            return cls._coerce_image_evidence_payloads(value.get("image_evidence"))
        if isinstance(value.get("image_evidences"), list):
            return cls._coerce_image_evidence_payloads(value.get("image_evidences"))
        if value.get("success") is False and "evidence" not in value:
            return []

        evidence_keys = {
            "image_evidence_id",
            "scene",
            "summary",
            "vehicle",
            "diagnosis",
            "visible_text",
            "suggested_queries",
        }
        if not any(key in value for key in evidence_keys):
            return []
        return [dict(value)]

    def _record_request_image_evidence(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        request: ChatRequest,
        session_id: str,
    ) -> None:
        payloads = self._extract_request_image_evidence_payloads(request)
        if not payloads:
            return

        manager = self._get_case_context_manager(active_deps)
        if manager is None or active_deps.case_context is None:
            return

        existing_ids = {
            str(getattr(artifact, "artifact_id", "") or "")
            for artifact in getattr(active_deps.case_context, "artifacts", [])
        }
        context = active_deps.case_context
        recorded_count = 0
        for payload in payloads:
            evidence_id = str(payload.get("image_evidence_id") or "").strip()
            if evidence_id and evidence_id in existing_ids:
                continue
            context = manager.record_image_evidence(context, evidence=payload)
            if evidence_id:
                existing_ids.add(evidence_id)
            recorded_count += 1

        if recorded_count <= 0:
            return

        active_deps.case_context = manager.save(
            manager.attach_runtime_state(context, loop_guard=active_deps.loop_guard)
        )
        active_deps.tracer.trace(
            event_type="image_evidence_recorded",
            session_id=session_id,
            payload={"count": recorded_count},
        )

    @classmethod
    def _collect_image_evidence_payloads(cls, case_context: Any | None) -> list[dict[str, Any]]:
        if case_context is None:
            return []
        payloads: list[dict[str, Any]] = []
        for artifact in getattr(case_context, "artifacts", []) or []:
            artifact_type = getattr(getattr(artifact, "type", None), "value", getattr(artifact, "type", None))
            if artifact_type != "image_evidence":
                continue
            structured_data = getattr(artifact, "structured_data", None)
            if isinstance(structured_data, dict):
                payloads.append(structured_data)
        return payloads

    @classmethod
    def _collect_request_and_case_image_evidence(
        cls,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
    ) -> list[dict[str, Any]]:
        payloads = [
            *cls._extract_request_image_evidence_payloads(request),
            *cls._collect_image_evidence_payloads(getattr(active_deps, "case_context", None)),
        ]
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in payloads:
            evidence_id = str(payload.get("image_evidence_id") or "").strip()
            if not evidence_id:
                evidence_id = json.dumps(payload, sort_keys=True, ensure_ascii=False)[:512]
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            deduped.append(payload)
        return deduped

    @classmethod
    def _build_image_evidence_summary(cls, payloads: Sequence[dict[str, Any]] | None) -> str:
        if not payloads:
            return ""

        lines: list[str] = []
        for index, payload in enumerate(list(payloads)[:3], start=1):
            scene = str(payload.get("scene") or "unknown").strip()
            summary = str(payload.get("summary") or "").strip()
            vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
            diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}

            vehicle_parts = [
                vehicle.get("brand"),
                vehicle.get("series"),
                vehicle.get("model"),
                vehicle.get("engine"),
                vehicle.get("emission"),
            ]
            vehicle_text = " ".join(str(item).strip() for item in vehicle_parts if str(item or "").strip())
            fault_codes = [str(item).strip() for item in diagnosis.get("fault_codes") or [] if str(item).strip()]
            descriptions = [
                str(item).strip()
                for item in diagnosis.get("descriptions") or []
                if str(item).strip()
            ][:2]
            suggested_queries = [
                str(item).strip()
                for item in payload.get("suggested_queries") or []
                if str(item).strip()
            ][:2]
            visible_text = [
                str(item).strip()
                for item in payload.get("visible_text") or []
                if str(item).strip()
            ][:2]

            parts = [f"{index}. 场景={scene}"]
            if summary:
                parts.append(f"摘要={summary[:160]}")
            if vehicle_text:
                parts.append(f"车辆={vehicle_text}")
            if fault_codes:
                parts.append(f"故障码={', '.join(fault_codes[:5])}")
            if diagnosis.get("ecu_model"):
                parts.append(f"ECU={diagnosis.get('ecu_model')}")
            if descriptions:
                parts.append(f"报码描述={'；'.join(descriptions)}")
            if suggested_queries:
                parts.append(f"建议查询={'；'.join(suggested_queries)}")
            elif visible_text:
                parts.append(f"可见文字={'；'.join(item[:100] for item in visible_text)}")
            lines.append("；".join(parts))
        return "\n".join(lines)

    @classmethod
    def _infer_business_from_image_evidence_payloads(
        cls,
        payloads: Sequence[dict[str, Any]] | None,
    ) -> str | None:
        if not payloads:
            return None
        for payload in payloads:
            diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}
            if diagnosis.get("fault_codes"):
                return "FAULT_DIAGNOSIS"

        for payload in payloads:
            scene = str(payload.get("scene") or "").strip()
            vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
            if scene in {"vehicle_identity", "document_hint"} and any(
                vehicle.get(key) for key in ("brand", "series", "model", "engine", "emission")
            ):
                return "DOC_SEARCH"
            if scene == "document_hint" and payload.get("suggested_queries"):
                return "DOC_SEARCH"

        for payload in payloads:
            scene = str(payload.get("scene") or "").strip()
            if scene in {"diagnostic_screen", "repair_scene"}:
                return "GENERAL_CHAT"
            if payload.get("summary") or payload.get("visible_text"):
                return "GENERAL_CHAT"
        return None

    @classmethod
    def _first_image_evidence_fault_code_from_request(cls, request: ChatRequest) -> str | None:
        for payload in cls._extract_request_image_evidence_payloads(request):
            diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}
            fault_codes = diagnosis.get("fault_codes") or []
            if isinstance(fault_codes, list) and fault_codes:
                return str(fault_codes[0]).strip() or None
        return None

    def _build_intent_router_text_with_image_evidence(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
    ) -> str:
        message = (request.message or "").strip()
        evidence_summary = self._build_image_evidence_summary(
            self._collect_request_and_case_image_evidence(request=request, active_deps=active_deps)
        )
        if not evidence_summary:
            return message
        if message:
            return f"{message}\n\n[IMAGE_EVIDENCE]\n{evidence_summary}\n[/IMAGE_EVIDENCE]"
        return f"用户上传了汽车维修相关图片，请根据图片证据判断意图。\n[IMAGE_EVIDENCE]\n{evidence_summary}\n[/IMAGE_EVIDENCE]"

    @classmethod
    def _build_intent_router_text_from_request_context(cls, request: ChatRequest) -> str:
        message = (request.message or "").strip()
        evidence_summary = cls._build_image_evidence_summary(cls._extract_request_image_evidence_payloads(request))
        if not evidence_summary:
            return message
        if message:
            return f"{message}\n\n[IMAGE_EVIDENCE]\n{evidence_summary}\n[/IMAGE_EVIDENCE]"
        return f"用户上传了汽车维修相关图片。\n[IMAGE_EVIDENCE]\n{evidence_summary}\n[/IMAGE_EVIDENCE]"

    def _build_query_with_image_evidence(self, query: str, case_context: Any | None) -> str:
        base_query = str(query or "").strip()
        lowered_query = base_query.lower()
        prefix_parts: list[str] = []
        suffix_parts: list[str] = []

        slots = getattr(case_context, "slots", None)
        if slots is not None:
            for key in ("brand", "series", "model", "engine", "emission", "ecu_model", "fault_code"):
                value = getattr(slots, key, None)
                text = str(value or "").strip()
                if text and text.lower() not in lowered_query and text not in prefix_parts:
                    prefix_parts.append(text)

        for payload in self._collect_image_evidence_payloads(case_context):
            vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
            diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}
            for value in [
                vehicle.get("brand"),
                vehicle.get("series"),
                vehicle.get("model"),
                vehicle.get("engine"),
                vehicle.get("emission"),
                diagnosis.get("ecu_model"),
                *((diagnosis.get("fault_codes") or [])[:3] if isinstance(diagnosis.get("fault_codes"), list) else []),
            ]:
                text = str(value or "").strip()
                if text and text.lower() not in lowered_query and text not in prefix_parts:
                    prefix_parts.append(text)
            for value in (payload.get("suggested_queries") or [])[:2]:
                text = str(value or "").strip()
                if text and text.lower() not in lowered_query and text not in suffix_parts:
                    suffix_parts.append(text)

        if not base_query and suffix_parts:
            base_query = suffix_parts.pop(0)
            lowered_query = base_query.lower()
            prefix_parts = [part for part in prefix_parts if part.lower() not in lowered_query]
            suffix_parts = [part for part in suffix_parts if part.lower() not in lowered_query]

        parts = [*prefix_parts, base_query, *suffix_parts]
        return " ".join(part for part in parts if part).strip()

    def _build_image_evidence_user_prompt(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        include_summary: bool,
    ) -> str | None:
        payloads = self._collect_request_and_case_image_evidence(request=request, active_deps=active_deps)
        if not payloads:
            return None

        business = self._infer_business_from_image_evidence_payloads(payloads)
        if business == "FAULT_DIAGNOSIS":
            prompt = "请根据用户上传图片中识别出的故障码、ECU和报码描述继续做故障诊断。"
        elif business == "DOC_SEARCH":
            prompt = "请根据用户上传图片中识别出的车辆和资料线索继续检索相关资料。"
        else:
            prompt = "请根据用户上传图片中识别出的车辆、诊断仪文字和现场信息继续处理本轮请求。"

        if not include_summary:
            return prompt

        evidence_summary = self._build_image_evidence_summary(payloads)
        if not evidence_summary:
            return prompt
        return f"{prompt}\n[IMAGE_EVIDENCE]\n{evidence_summary}\n[/IMAGE_EVIDENCE]"

    @staticmethod
    def _build_doc_search_known_slots_text(case_context: Any | None) -> str:
        slots = getattr(case_context, "slots", None)
        if slots is None:
            return ""

        lines: list[str] = []
        for key, label in (
            ("brand", "品牌"),
            ("series", "车系"),
            ("model", "车型"),
            ("engine", "发动机"),
            ("emission", "排放"),
            ("ecu_model", "ECU"),
            ("fault_code", "故障码"),
        ):
            value = getattr(slots, key, None)
            text = str(value or "").strip()
            if text:
                lines.append(f"{label}={text}")
        return "；".join(lines)

    @classmethod
    def _normalize_doc_search_query_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None

        text = text.replace("，", " ").replace(",", " ").replace("；", " ").replace(";", " ")
        text = cls._DOC_SEARCH_QUERY_SLASH_RE.sub(" ", text)
        text = text.replace("_", " ")
        text = cls._DOC_SEARCH_QUERY_SPACE_RE.sub(" ", text)
        text = cls._DOC_SEARCH_QUERY_EDGE_RE.sub("", text)
        text = cls._DOC_SEARCH_QUERY_SPACE_RE.sub(" ", text).strip()
        return text or None

    @classmethod
    def _extract_doc_search_code_candidates(cls, text: str) -> tuple[str, ...]:
        normalized = str(text or "").upper()
        if not normalized:
            return tuple()

        alpha_numeric_tokens = [
            str(token or "").strip("()[]{}<>，。；;：:,_/\\ ")
            for token in cls._DOC_SEARCH_IMAGE_CODE_RE.findall(normalized)
        ]
        candidates: list[str] = []
        seen: set[str] = set()
        for token in alpha_numeric_tokens:
            cleaned = str(token or "").strip("()[]{}<>，。；;：:,_/\\ ")
            if len(cleaned) < 4:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            candidates.append(cleaned)

        for token in cls._DOC_SEARCH_NUMERIC_CODE_RE.findall(normalized):
            cleaned = str(token or "").strip("()[]{}<>，。；;：:,_/\\ ")
            if len(cleaned) < 4:
                continue
            if any(cleaned in existing for existing in alpha_numeric_tokens):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            candidates.append(cleaned)
        return tuple(candidates)

    @classmethod
    def _score_doc_search_code_candidate(
        cls,
        token: str,
        *,
        source_text: str,
        from_suggested_query: bool,
    ) -> int:
        has_alpha = any(ch.isalpha() for ch in token)
        has_digit = any(ch.isdigit() for ch in token)
        hyphen_count = token.count("-")
        score = 0

        if has_alpha and has_digit:
            score += 5
        elif token.isdigit():
            if 6 <= len(token) <= 10:
                score += 4
            elif len(token) <= 14:
                score += 2
            else:
                score += 1
        else:
            score -= 1

        if 4 <= len(token) <= 8:
            score += 3
        elif len(token) <= 12:
            score += 2
        elif len(token) <= 16:
            score += 1
        else:
            score -= 1

        if hyphen_count >= 2:
            score -= 1

        if re.fullmatch(r"[A-Z]{1,3}\d{2,}", token):
            score += 2
        if re.fullmatch(r"[A-Z]\d{4,}", token):
            score += 2
        if any(hint in token for hint in cls._DOC_SEARCH_CODE_PRIORITY_HINTS):
            score += 2
        if token.isdigit() and 6 <= len(token) <= 10:
            score += 3
        if token.startswith(("H-", "RN", "1RN")):
            score -= 2
        if "/" in source_text:
            score += 1
        if from_suggested_query:
            score += 4
        return score

    @classmethod
    def _extract_doc_search_doc_type_hints(cls, text: str) -> tuple[str, ...]:
        normalized = str(text or "")
        hints: list[str] = []
        for hint in cls._DOC_SEARCH_DOC_TYPE_HINTS:
            if hint in normalized and hint not in hints:
                hints.append(hint)
        return tuple(hints)

    @classmethod
    def _extract_doc_search_image_hint_queries(
        cls,
        payloads: Sequence[dict[str, Any]] | None,
    ) -> tuple[str, ...]:
        if not payloads:
            return tuple()

        code_scores: dict[str, int] = {}
        direct_queries: list[str] = []
        supplier_hints: list[str] = []
        brand_hints: list[str] = []

        for payload in payloads:
            vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
            brand = str(vehicle.get("brand") or "").strip()
            if brand and brand not in brand_hints:
                brand_hints.append(brand)

            raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
            raw_values: list[str] = []
            for value in raw.values():
                if isinstance(value, str):
                    raw_values.append(value)
                elif isinstance(value, list):
                    raw_values.extend(str(item) for item in value if item)

            summary = str(payload.get("summary") or "").strip()
            visible_text = [str(item).strip() for item in payload.get("visible_text") or [] if str(item).strip()]
            suggested_queries = [
                normalized
                for item in payload.get("suggested_queries") or []
                for normalized in [cls._normalize_doc_search_query_text(item)]
                if normalized
            ]
            for query in suggested_queries[:3]:
                if query not in direct_queries:
                    direct_queries.append(query)

            for text in [summary, *suggested_queries, *visible_text, *raw_values]:
                normalized = str(text or "").strip()
                if not normalized:
                    continue
                for token in cls._extract_doc_search_code_candidates(normalized):
                    score = cls._score_doc_search_code_candidate(
                        token,
                        source_text=normalized.upper(),
                        from_suggested_query=normalized in suggested_queries,
                    )
                    previous = code_scores.get(token)
                    if previous is None or score > previous:
                        code_scores[token] = score

                supplier_source = normalized in suggested_queries or normalized in visible_text or normalized in raw_values
                if not supplier_source:
                    continue

                for token in cls._DOC_SEARCH_CHINESE_HINT_RE.findall(normalized):
                    supplier = cls._normalize_doc_search_company_hint(token)
                    if supplier and supplier not in supplier_hints:
                        supplier_hints.append(supplier)

        prioritized_codes = sorted(code_scores.items(), key=lambda item: (-item[1], len(item[0]), item[0]))

        queries: list[str] = []

        def _queue(query_text: Any) -> None:
            normalized_query = cls._normalize_doc_search_query_text(query_text)
            if normalized_query and normalized_query not in queries:
                queries.append(normalized_query)

        for query in direct_queries[:3]:
            _queue(query)

        for code, _ in prioritized_codes[:5]:
            for supplier in supplier_hints[:2]:
                _queue(f"{supplier}{code}")
            _queue(code)
            for brand in brand_hints[:1]:
                _queue(f"{brand} {code}")

        for supplier in supplier_hints[:2]:
            _queue(supplier)
            for brand in brand_hints[:1]:
                _queue(f"{brand} {supplier}")

        normalized_queries: list[str] = []
        seen_queries: set[str] = set()
        for item in queries:
            normalized = cls._normalize_doc_search_query_text(item)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            normalized_queries.append(normalized)

        return tuple(normalized_queries[:12])

    @classmethod
    def _normalize_doc_search_company_hint(cls, text: str) -> str | None:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        if not normalized:
            return None
        normalized = cls._DOC_SEARCH_COMPANY_SUFFIX_RE.sub("", normalized)
        normalized = cls._DOC_SEARCH_CITY_PREFIX_RE.sub("", normalized)
        normalized = normalized.strip("，。、；：-_/ ")
        if len(normalized) < 2:
            return None
        if normalized in cls._DOC_SEARCH_GENERIC_HINT_WORDS:
            return None
        if any(hint in normalized for hint in cls._DOC_SEARCH_DOC_TYPE_HINTS):
            return None
        if len(normalized) > 4:
            normalized = normalized[-4:]
        return normalized

    @classmethod
    def _has_request_or_case_image_evidence(
        cls,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
    ) -> bool:
        return bool(cls._collect_request_and_case_image_evidence(request=request, active_deps=active_deps))

    async def _plan_doc_search_queries(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        fallback_query: str,
    ) -> tuple[str, tuple[DocSearchExecutedQuery, ...], str, str]:
        fallback = str(fallback_query or "").strip()
        payloads = self._collect_request_and_case_image_evidence(request=request, active_deps=active_deps)
        request_text = (request.message or "").strip()
        has_text = bool(request_text)
        has_image_evidence = bool(payloads)
        if has_text and has_image_evidence:
            input_mode = "text_image"
        elif has_image_evidence:
            input_mode = "image"
        else:
            input_mode = "text"

        if not fallback and not request_text and not has_image_evidence:
            return "", tuple(), "", ""

        planner = PydanticAIDocSearchQueryPlanner(config_service=active_deps.config_service)
        image_evidence = self._build_image_evidence_summary(
            payloads
        )
        known_slots = self._build_doc_search_known_slots_text(active_deps.case_context)
        image_hint_queries = self._extract_doc_search_image_hint_queries(payloads)

        plan = await planner.plan(
            query=request_text or fallback,
            image_evidence=image_evidence,
            known_slots=known_slots,
            input_mode=input_mode,
        )
        executed: list[DocSearchExecutedQuery] = []
        seen: set[str] = set()

        def _append_query(query_text: Any, confidence: float) -> None:
            normalized_query = self._normalize_doc_search_query_text(query_text)
            if not normalized_query:
                return
            key = normalized_query.lower()
            if key in seen:
                return
            seen.add(key)
            executed.append(
                DocSearchExecutedQuery(
                    query=normalized_query,
                    confidence=confidence,
                )
            )

        primary_query = self._normalize_doc_search_query_text(fallback)
        rationale = ""
        body_keyword = ""
        planner_items: list[Any] = []
        if plan is not None:
            primary_query = self._normalize_doc_search_query_text(plan.primary_query) or primary_query
            rationale = str(plan.rationale or "").strip()
            body_keyword = str(plan.body_keyword or "").strip()
            planner_items = list(plan.queries)

        if primary_query:
            _append_query(primary_query, 1.0)

        if primary_query:
            planner_items = [
                item
                for item in planner_items
                if self._normalize_doc_search_query_text(item.query) != primary_query
            ]

        for item in planner_items:
            _append_query(item.query, float(item.confidence or 0.5))

        for hint_query in image_hint_queries:
            normalized_hint_query = self._normalize_doc_search_query_text(hint_query)
            if not normalized_hint_query:
                continue
            _append_query(
                normalized_hint_query,
                0.9
                if re.search(r"[\u4e00-\u9fff].*[A-Z0-9]|[A-Z0-9].*[\u4e00-\u9fff]", normalized_hint_query)
                else 0.86,
            )

        if not executed:
            if not fallback:
                return "", tuple(), "", body_keyword
            return fallback, (DocSearchExecutedQuery(query=fallback, confidence=1.0),), "", body_keyword

        return executed[0].query, tuple(executed[:10]), rationale, body_keyword

    @staticmethod
    def _is_better_doc_search_result_candidate(
        candidate: dict[str, Any],
        current: dict[str, Any] | None,
    ) -> bool:
        if current is None:
            return True
        candidate_rank = (
            float(candidate.get("score") or 0.0),
            float(candidate.get("matched_query_confidence") or 0.0),
        )
        current_rank = (
            float(current.get("score") or 0.0),
            float(current.get("matched_query_confidence") or 0.0),
        )
        return candidate_rank > current_rank

    @staticmethod
    def _merge_doc_search_envelopes(
        envelopes: Sequence[tuple[DocSearchExecutedQuery, dict[str, Any]]],
        *,
        primary_query: str,
        rationale: str = "",
    ) -> dict[str, Any]:
        valid_items = [
            (query_info, envelope)
            for query_info, envelope in envelopes
            if isinstance(envelope, dict) and envelope.get("status") == "ok" and isinstance(envelope.get("data"), dict)
        ]
        if not valid_items:
            for _, envelope in envelopes:
                if isinstance(envelope, dict):
                    return envelope
            return {"status": "failed", "data": {"message": "资料搜索失败。"}}

        first_data = dict(valid_items[0][1].get("data") or {})
        merged_results: dict[str, dict[str, Any]] = {}
        search_methods: list[str] = []
        total = 0
        total_search_time_ms = 0.0
        primary_preprocessing: dict[str, Any] | None = None
        ranked_preprocessings: list[tuple[float, float, dict[str, Any]]] = []
        fallback_preprocessings: list[tuple[float, dict[str, Any]]] = []
        planned_queries: list[dict[str, Any]] = []

        for query_info, envelope in valid_items:
            data = envelope.get("data") or {}
            raw_results = list(data.get("results") or [])
            total += len(raw_results)
            search_time = data.get("search_time_ms")
            if isinstance(search_time, (int, float)):
                total_search_time_ms += float(search_time)
            search_method = str(data.get("search_method") or "").strip()
            if search_method and search_method not in search_methods:
                search_methods.append(search_method)
            planned_queries.append(
                {
                    "query": query_info.query,
                    "confidence": query_info.confidence,
                    "hit_count": len(raw_results),
                }
            )

            preprocessing = data.get("preprocessing")
            if isinstance(preprocessing, dict):
                preprocessing_copy = json.loads(json.dumps(preprocessing, ensure_ascii=False))
                if (
                    primary_query
                    and str(data.get("query") or query_info.query or "").strip().lower() == primary_query.strip().lower()
                    and primary_preprocessing is None
                ):
                    primary_preprocessing = preprocessing_copy
                if raw_results:
                    top_score = max(float(item.get("score") or 0.0) for item in raw_results)
                    ranked_preprocessings.append((top_score, query_info.confidence, preprocessing_copy))
                else:
                    fallback_preprocessings.append((query_info.confidence, preprocessing_copy))

            for item in raw_results:
                file_id = str(item.get("file_id") or "").strip()
                dedupe_key = file_id or json.dumps(item, sort_keys=True, ensure_ascii=False)
                merged_item = dict(item)
                merged_item.setdefault("matched_query", query_info.query)
                merged_item.setdefault("matched_query_confidence", query_info.confidence)
                if AgentLoopService._is_better_doc_search_result_candidate(
                    merged_item,
                    merged_results.get(dedupe_key),
                ):
                    merged_results[dedupe_key] = merged_item

        ordered_results = sorted(
            merged_results.values(),
            key=lambda item: (
                -float(item.get("score") or 0.0),
                -float(item.get("matched_query_confidence") or 0.0),
            )
        )

        preprocessing_candidates: list[dict[str, Any]] = []
        seen_preprocessing: set[str] = set()

        def _append_preprocessing(candidate: dict[str, Any] | None) -> None:
            if not isinstance(candidate, dict) or not candidate:
                return
            key = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
            if key in seen_preprocessing:
                return
            seen_preprocessing.add(key)
            preprocessing_candidates.append(candidate)

        _append_preprocessing(primary_preprocessing)
        for _, _, preprocessing in sorted(ranked_preprocessings, key=lambda item: (-item[0], -item[1])):
            _append_preprocessing(preprocessing)
        for _, preprocessing in sorted(fallback_preprocessings, key=lambda item: -item[0]):
            _append_preprocessing(preprocessing)
        if not preprocessing_candidates and isinstance(first_data.get("preprocessing"), dict):
            _append_preprocessing(first_data.get("preprocessing"))

        first_data["query"] = primary_query or first_data.get("query") or first_data.get("original_query") or ""
        first_data["original_query"] = primary_query or first_data.get("original_query") or first_data.get("query") or ""
        first_data["results"] = ordered_results
        first_data["total"] = len(ordered_results) if ordered_results else total
        first_data["search_method"] = "+".join(search_methods) if search_methods else first_data.get("search_method")
        first_data["search_time_ms"] = total_search_time_ms or first_data.get("search_time_ms")
        first_data.pop("validity", None)
        first_data.pop("summary", None)
        first_data.pop("summary_query", None)
        first_data.pop("result_summary", None)
        if preprocessing_candidates:
            first_data["preprocessing"] = preprocessing_candidates[0]
            first_data["validation_preprocessing_candidates"] = preprocessing_candidates
        if rationale or len(planned_queries) > 1:
            first_data["planned_queries"] = planned_queries
        else:
            first_data.pop("planned_queries", None)
        if rationale:
            first_data["query_plan_rationale"] = rationale

        return {"status": "ok", "data": first_data}

    async def _execute_planned_doc_search(
        self,
        *,
        adapter: LegacyDocSearchAdapter,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        workflow_state: DocSearchWorkflowRunState,
        selection_payload: dict[str, Any] | None,
    ) -> DocSearchPlannedSearchResult:
        primary_query, executed_queries, rationale, body_keyword = await self._plan_doc_search_queries(
            request=request,
            active_deps=active_deps,
            fallback_query=workflow_state.query,
        )

        envelopes: list[tuple[DocSearchExecutedQuery, dict[str, Any]]] = []
        for query_info in executed_queries:
            envelope = await adapter.search_raw(
                query=query_info.query,
            )
            envelopes.append((query_info, envelope))

        merged_snapshot_envelope = self._merge_doc_search_envelopes(
            envelopes,
            primary_query=primary_query or workflow_state.query,
            rationale=rationale,
        )
        if merged_snapshot_envelope.get("status") != "ok":
            return DocSearchPlannedSearchResult(
                envelope=merged_snapshot_envelope,
                executed_queries=executed_queries,
                primary_query=primary_query or workflow_state.query,
                rationale=rationale,
                body_keyword=body_keyword,
            )

        merged_snapshot = dict(merged_snapshot_envelope.get("data") or {})
        merged_snapshot["original_user_query"] = workflow_state.original_query or workflow_state.query
        preprocessing_candidates = merged_snapshot.pop("validation_preprocessing_candidates", None)
        planned_queries = merged_snapshot.get("planned_queries") or []
        final_envelope: dict[str, Any] | None = None

        candidate_preprocessings = [
            item
            for item in (preprocessing_candidates or [])
            if isinstance(item, dict)
        ]
        if not candidate_preprocessings and isinstance(merged_snapshot.get("preprocessing"), dict):
            candidate_preprocessings = [merged_snapshot.get("preprocessing")]

        snapshots_to_try = candidate_preprocessings or [None]
        for preprocessing in snapshots_to_try:
            snapshot = dict(merged_snapshot)
            if preprocessing is not None:
                snapshot["preprocessing"] = preprocessing
            final_envelope = await adapter.search_from_snapshot(
                query=primary_query or workflow_state.query,
                snapshot=snapshot,
                selection_payload=selection_payload,
            )
            if not isinstance(final_envelope, dict) or final_envelope.get("status") != "ok":
                break
            validity = (final_envelope.get("data") or {}).get("validity") or {}
            if validity.get("has_valid_results") is not False:
                break

        merged = final_envelope or merged_snapshot_envelope
        if merged.get("status") == "ok" and isinstance(merged.get("data"), dict):
            merged_data = merged["data"]
            if rationale or len(planned_queries) > 1:
                merged_data["planned_queries"] = planned_queries
            else:
                merged_data.pop("planned_queries", None)
            if rationale:
                merged_data["query_plan_rationale"] = rationale
            if body_keyword:
                merged_data["body_keyword"] = body_keyword
                merged_data["circuit_body_keyword"] = body_keyword

        return DocSearchPlannedSearchResult(
            envelope=merged,
            executed_queries=executed_queries,
            primary_query=primary_query or workflow_state.query,
            rationale=rationale,
            body_keyword=body_keyword,
        )

    async def _resolve_request_intent(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
    ) -> IntentDecision:
        started_at = time.perf_counter()
        router = RequestIntentRouter(
            fault_code_parser=active_deps.fault_code_parser,
            diagnosis_enabled_provider=self._is_diagnosis_enabled,
            config_service=active_deps.config_service,
            llm_observer=lambda result, llm_started_at, phase: self._record_llm_run_observability(
                active_deps=active_deps,
                session_id=session_id,
                result=result,
                llm_started_at=llm_started_at,
                phase=phase,
            ),
        )
        router_text = self._build_intent_router_text_with_image_evidence(request=request, active_deps=active_deps)
        high_confidence = router.route_high_confidence(router_text)
        if high_confidence is not None:
            self._cache_intent_decision(request, high_confidence)
            self._trace_intent_router_decision(
                active_deps=active_deps,
                session_id=session_id,
                decision=high_confidence,
                started_at=started_at,
            )
            return high_confidence

        cached = self._cached_intent_decision(request)
        if cached is not None:
            return cached

        decision = await router.route_async(router_text, request.mode)
        self._cache_intent_decision(request, decision)

        self._trace_intent_router_decision(
            active_deps=active_deps,
            session_id=session_id,
            decision=decision,
            started_at=started_at,
        )
        return decision

    @staticmethod
    def _trace_intent_router_decision(
        *,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        decision: IntentDecision,
        started_at: float,
    ) -> None:
        tracer = getattr(active_deps, "tracer", None)
        if tracer is None:
            return
        tracer.trace(
            event_type="intent_router_decision",
            session_id=session_id,
            payload={
                "intent": decision.intent.value,
                "reason": decision.reason,
                "source": decision.source,
                "confidence": decision.confidence,
                "normalized_fault_code": decision.normalized_fault_code,
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )

    async def process(self, request: ChatRequest, runtime_deps: AgentRuntimeDeps | None = None) -> ChatResponse:
        status = self._status
        session_id = request.session_id or uuid4().hex
        active_deps = self._prepare_request_runtime_deps(
            runtime_deps=runtime_deps or self._deps,
            request=request,
            session_id=session_id,
        )
        request_id = uuid4().hex
        active_deps.tracer.trace(
            event_type="agent_loop_request_scope",
            session_id=session_id,
            payload=self._request_trace_payload(active_deps, request_id),
        )
        self._record_request_image_evidence(active_deps=active_deps, request=request, session_id=session_id)
        if (request.message or "").strip() or self._extract_request_image_evidence_payloads(request):
            await self._resolve_request_intent(request=request, active_deps=active_deps, session_id=session_id)

        doc_search_state = self._resolve_doc_search_workflow_state(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
        )
        if doc_search_state is not None:
            return await self._process_doc_search_workflow(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                workflow_state=doc_search_state,
            )

        parameter_query_state = self._resolve_parameter_query_workflow_state(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
        )
        if parameter_query_state is not None:
            return await self._process_parameter_query_workflow(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                workflow_state=parameter_query_state,
            )

        if not status.available:
            active_deps.tracer.trace(
                event_type="agent_loop_not_ready",
                session_id=session_id,
                detail=status.reason,
            )
            return self._error_response(
                deps=active_deps,
                request_id=request_id,
                session_id=session_id,
                error_code="AGENT_RUNTIME_NOT_READY",
                message="Pydantic AI runtime is not available.",
                detail=status.reason,
            )

        agent, repair_gate_agent, repair_render_planner_agent, repair_renderer_agent = self._resolve_request_agents(active_deps)
        if agent is None:
            return self._error_response(
                deps=active_deps,
                request_id=request_id,
                session_id=session_id,
                error_code="AGENT_RUNTIME_NOT_READY",
                message="Pydantic AI runtime is not available.",
                detail="Agent creation failed for the current runtime config.",
            )

        message_history: Sequence[Any] | None = None
        deferred_tool_results = None
        captured_messages: list[Any] | None = None
        user_prompt: str | None = None
        repair_gate_result: RepairAnswerGateReadyState | ChatResponse | None = None
        repair_render_state: RepairRenderRuntimeState | None = None
        llm_started_at: float | None = None
        try:
            message_history, deferred_tool_results = self._prepare_run_state(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                error_as_response=True,
            )
            if isinstance(message_history, ChatResponse):
                return message_history

            if request.ask_user_answer is not None:
                self._record_case_context_user_answer(active_deps=active_deps, answer=request.ask_user_answer)

            user_prompt = self._build_user_prompt_with_case_context(
                active_deps=active_deps,
                request=request,
                message_history=message_history,
            )
            if user_prompt is None and message_history is None and deferred_tool_results is None:
                return self._error_response(
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    error_code="EMPTY_REQUEST",
                    message="Either `message` or a deferred ask_user_answer payload is required.",
                )

            active_deps.tracer.trace(
                event_type="agent_loop_run_start",
                session_id=session_id,
                payload={
                    "has_history": bool(message_history),
                    "has_deferred_results": deferred_tool_results is not None,
                    "mode": request.mode,
                },
            )

            if self._should_use_repair_answer_gate(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                repair_gate_agent=repair_gate_agent,
                repair_renderer_agent=repair_renderer_agent,
            ):
                repair_gate_result = await self._run_repair_answer_gate(
                    request=request,
                    active_deps=active_deps,
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=status.version,
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                    user_prompt=user_prompt,
                    repair_gate_agent=repair_gate_agent,
                    repair_renderer_agent=repair_renderer_agent,
                )
                if isinstance(repair_gate_result, ChatResponse):
                    return repair_gate_result
                if repair_gate_result is not None:
                    if repair_renderer_agent is not None:
                        repair_render_state = await self._prepare_repair_render_runtime_state(
                            request=request,
                            active_deps=active_deps,
                            ready_state=repair_gate_result,
                            repair_render_planner_agent=repair_render_planner_agent,
                        )
                        message_history = repair_render_state.message_history
                        user_prompt = repair_render_state.user_prompt
                        deferred_tool_results = None
                    else:
                        message_history = repair_gate_result.message_history
                        user_prompt = repair_gate_result.query
                        deferred_tool_results = None

            from pydantic_ai import DeferredToolRequests
            from pydantic_ai import capture_run_messages

            with capture_run_messages() as captured_messages:
                llm_started_at = time.perf_counter()
                result = await (
                    repair_renderer_agent if repair_gate_result is not None else agent
                ).run(
                    user_prompt=user_prompt,
                    deps=active_deps,
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                )
            self._record_llm_run_observability(
                active_deps=active_deps,
                session_id=session_id,
                result=result,
                llm_started_at=llm_started_at,
                phase="agent_loop",
            )

            serialized_history = result.all_messages_json().decode("utf-8")
            output = result.output
            if repair_render_state is not None:
                output, serialized_history = await self._maybe_retry_repair_render_output(
                    active_deps=active_deps,
                    repair_renderer_agent=repair_renderer_agent,
                    render_state=repair_render_state,
                    output=output,
                    serialized_history=serialized_history,
                )
            active_deps.message_history_store.save_serialized_history(session_id, serialized_history)
            full_messages = self._deserialize_history(serialized_history)

            run_messages = self._current_run_messages(
                full_messages=full_messages,
                message_history=message_history,
            )

            if isinstance(output, DeferredToolRequests):
                ask_user = self._extract_ask_user_question(output)
                if ask_user is None:
                    return self._error_response(
                        deps=active_deps,
                        request_id=request_id,
                        session_id=session_id,
                        error_code="UNSUPPORTED_DEFERRED_TOOL_REQUEST",
                        message="The runtime returned a deferred tool request that is not mapped yet.",
                    )
                ask_user = await self._normalize_runtime_ask_user_question_async(
                    ask_user=ask_user,
                    request=request,
                    full_messages=full_messages,
                )

                self._save_agent_ask_user_state(
                    active_deps=active_deps,
                    session_id=session_id,
                    serialized_history=serialized_history,
                    full_messages=full_messages,
                    ask_user=ask_user,
                    business=self._infer_business_from_messages(
                        run_messages,
                        request,
                        fallback_messages=full_messages,
                    ),
                    deferred_requests=result.output,
                )
                self._persist_case_context_after_agent_run(
                    active_deps=active_deps,
                    run_messages=run_messages,
                    request=request,
                    ask_user=ask_user,
                    business=self._infer_business_from_messages(
                        run_messages,
                        request,
                        fallback_messages=full_messages,
                    ),
                )
                active_deps.tracer.trace(
                    event_type="agent_loop_ask_user",
                    session_id=session_id,
                    payload={"tool_call_id": ask_user.tool_call_id, "question": ask_user.question},
                )
                return self._build_ask_user_response(
                    ask_user=ask_user,
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=status.version,
                    business=self._infer_business_from_messages(
                        run_messages,
                        request,
                        fallback_messages=full_messages,
                    ),
                    llm_observability=getattr(active_deps, "llm_observability", None),
                )

            active_deps.tracer.trace(
                event_type="agent_loop_run_done",
                session_id=session_id,
                payload={"output_type": type(output).__name__},
            )
            synthetic_repair_followup = await self._try_build_synthetic_repair_followup_response_async(
                request=request,
                active_deps=active_deps,
                full_messages=full_messages,
                serialized_history=serialized_history,
                content=output,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
            )
            if synthetic_repair_followup is not None:
                self._persist_case_context_after_agent_run(
                    active_deps=active_deps,
                    run_messages=run_messages,
                    request=request,
                    ask_user=synthetic_repair_followup.ask_user,
                    business="GENERAL_CHAT",
                )
                return synthetic_repair_followup

            self._persist_case_context_after_agent_run(
                active_deps=active_deps,
                run_messages=run_messages,
                request=request,
                ask_user=None,
                business=None,
            )

            response = self._try_extract_structured_response(
                request=request,
                active_deps=active_deps,
                messages=run_messages,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
            )
            if response is not None:
                return response

            fallback_param_response = self._try_recover_standalone_param_response(
                request=request,
                active_deps=active_deps,
                messages=run_messages,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
            )
            if fallback_param_response is not None:
                return fallback_param_response

            repair_knowledge_metadata = self._extract_repair_knowledge_metadata(full_messages)
            if repair_render_state is not None:
                final_content, repair_knowledge_metadata = self._finalize_repair_rendered_content(
                    content=output,
                    extra_metadata=repair_knowledge_metadata,
                    render_state=repair_render_state,
                )
            else:
                final_content, repair_knowledge_metadata = self._maybe_rewrite_repair_followup_message(
                    request=request,
                    active_deps=active_deps,
                    session_id=session_id,
                    full_messages=full_messages,
                    content=result.output,
                    extra_metadata=repair_knowledge_metadata,
                )
            return self._build_message_response(
                content=final_content,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                business=self._infer_message_business(
                    run_messages,
                    request,
                    fallback_messages=full_messages,
                ),
                extra_metadata=repair_knowledge_metadata,
                llm_observability=getattr(active_deps, "llm_observability", None),
            )
        except LoopGuardExceededError as exc:
            convergence = self._handle_guard_exceeded(
                exc=exc,
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                message_history=message_history,
                captured_messages=captured_messages,
            )
            active_deps.tracer.trace(
                event_type="agent_loop_guard_exceeded",
                session_id=session_id,
                detail=str(exc),
                payload={
                    "budget": self._guard_budget_snapshot(active_deps),
                    "convergence_mode": convergence.mode,
                },
            )
            return convergence.response
        except Exception as exc:
            active_deps.tracer.trace(
                event_type="agent_loop_error",
                session_id=session_id,
                detail=str(exc),
            )
            return self._error_response(
                deps=active_deps,
                request_id=request_id,
                session_id=session_id,
                error_code="AGENT_RUNTIME_ERROR",
                message=self._public_runtime_error_message(exc),
                detail=str(exc),
            )

    async def stream(
        self,
        request: ChatRequest,
        runtime_deps: AgentRuntimeDeps | None = None,
    ) -> AsyncIterator[AgentRuntimeEvent]:
        status = self._status
        session_id = request.session_id or uuid4().hex
        active_deps = self._prepare_request_runtime_deps(
            runtime_deps=runtime_deps or self._deps,
            request=request,
            session_id=session_id,
        )
        request_id = uuid4().hex
        active_deps.tracer.trace(
            event_type="agent_loop_stream_request_scope",
            session_id=session_id,
            payload=self._request_trace_payload(active_deps, request_id),
        )
        self._record_request_image_evidence(active_deps=active_deps, request=request, session_id=session_id)
        if (request.message or "").strip() or self._extract_request_image_evidence_payloads(request):
            await self._resolve_request_intent(request=request, active_deps=active_deps, session_id=session_id)

        doc_search_state = self._resolve_doc_search_workflow_state(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
        )
        if doc_search_state is not None:
            async for event in self._stream_doc_search_workflow(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                workflow_state=doc_search_state,
            ):
                yield event
            return

        parameter_query_state = self._resolve_parameter_query_workflow_state(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
        )
        if parameter_query_state is not None:
            async for event in self._stream_parameter_query_workflow(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                workflow_state=parameter_query_state,
            ):
                yield event
            return

        if not status.available:
            yield AgentRuntimeEvent(
                type=AgentEventType.ERROR,
                session_id=session_id,
                message="Pydantic AI runtime is not available.",
                metadata={"reason": status.reason, "request_id": request_id},
            )
            return

        agent, repair_gate_agent, repair_render_planner_agent, repair_renderer_agent = self._resolve_request_agents(active_deps)
        if agent is None:
            yield AgentRuntimeEvent(
                type=AgentEventType.ERROR,
                session_id=session_id,
                message="Pydantic AI runtime is not available.",
                metadata={
                    "reason": "Agent creation failed for the current runtime config.",
                    "request_id": request_id,
                },
            )
            return

        message_history: Sequence[Any] | None = None
        deferred_tool_results = None
        captured_messages: list[Any] | None = None
        streamed_chunks: list[str] = []
        user_prompt: str | None = None
        repair_gate_result: RepairAnswerGateReadyState | ChatResponse | None = None
        repair_render_state: RepairRenderRuntimeState | None = None
        llm_started_at: float | None = None
        first_response_at: float | None = None
        try:
            message_history, deferred_tool_results = self._prepare_run_state(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                error_as_response=False,
            )
            if isinstance(message_history, AgentRuntimeEvent):
                yield message_history
                return

            if request.ask_user_answer is not None:
                self._record_case_context_user_answer(active_deps=active_deps, answer=request.ask_user_answer)

            user_prompt = self._build_user_prompt_with_case_context(
                active_deps=active_deps,
                request=request,
                message_history=message_history,
            )
            if user_prompt is None and message_history is None and deferred_tool_results is None:
                yield AgentRuntimeEvent(
                    type=AgentEventType.ERROR,
                    session_id=session_id,
                    message="Either `message` or a deferred ask_user_answer payload is required.",
                    metadata={"request_id": request_id},
                )
                return

            yield AgentRuntimeEvent(
                type=AgentEventType.START,
                session_id=session_id,
                metadata={"request_id": request_id},
            )
            yield AgentRuntimeEvent(
                type=AgentEventType.HINT,
                session_id=session_id,
                message="正在处理，请稍候...",
                metadata={"request_id": request_id},
            )

            stream_agent = agent
            stream_message_history = message_history
            stream_user_prompt = user_prompt
            stream_deferred_tool_results = deferred_tool_results
            buffer_text_deltas = False

            if self._should_use_repair_answer_gate(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                repair_gate_agent=repair_gate_agent,
                repair_renderer_agent=repair_renderer_agent,
            ):
                repair_gate_result = await self._run_repair_answer_gate(
                    request=request,
                    active_deps=active_deps,
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=status.version,
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                    user_prompt=user_prompt,
                    repair_gate_agent=repair_gate_agent,
                    repair_renderer_agent=repair_renderer_agent,
                )
                if isinstance(repair_gate_result, ChatResponse):
                    yield AgentRuntimeEvent(
                        type=AgentEventType.DONE,
                        session_id=session_id,
                        metadata={
                            "request_id": request_id,
                            "response": repair_gate_result.model_dump(mode="json"),
                            "full_content": self._response_stream_full_content(repair_gate_result),
                        },
                    )
                    return
                if repair_gate_result is not None:
                    if repair_renderer_agent is not None:
                        stream_agent = repair_renderer_agent
                        repair_render_state = await self._prepare_repair_render_runtime_state(
                            request=request,
                            active_deps=active_deps,
                            ready_state=repair_gate_result,
                            repair_render_planner_agent=repair_render_planner_agent,
                        )
                        stream_message_history = repair_render_state.message_history
                        stream_user_prompt = repair_render_state.user_prompt
                        stream_deferred_tool_results = None
                        buffer_text_deltas = True
                    else:
                        stream_message_history = repair_gate_result.message_history
                        stream_user_prompt = repair_gate_result.query
                        stream_deferred_tool_results = None

            self._active_streams[session_id] = ActiveStreamState(
                message_history=stream_message_history,
                user_prompt=stream_user_prompt,
            )
            active_deps.tracer.trace(
                event_type="agent_loop_stream_start",
                session_id=session_id,
                payload={
                    "has_history": bool(stream_message_history),
                    "has_deferred_results": stream_deferred_tool_results is not None,
                    "mode": request.mode,
                    "repair_gate_applied": repair_gate_result is not None,
                },
            )

            from pydantic_ai import DeferredToolRequests
            from pydantic_ai import capture_run_messages
            from pydantic_ai.exceptions import UserError

            if self._agent_supports_streaming(stream_agent):
                with capture_run_messages() as captured_messages_ctx:
                    captured_messages = captured_messages_ctx
                    llm_started_at = time.perf_counter()
                    async with stream_agent.run_stream(
                        user_prompt=stream_user_prompt,
                        deps=active_deps,
                        message_history=stream_message_history,
                        deferred_tool_results=stream_deferred_tool_results,
                    ) as result:
                        try:
                            async for chunk in result.stream_text(delta=True, debounce_by=None):
                                if not chunk:
                                    continue
                                if first_response_at is None:
                                    first_response_at = time.perf_counter()
                                streamed_chunks.append(chunk)
                                if buffer_text_deltas:
                                    continue
                                yield AgentRuntimeEvent(
                                    type=AgentEventType.TEXT_DELTA,
                                    session_id=session_id,
                                    content=chunk,
                                    metadata={"request_id": request_id},
                                )
                        except UserError:
                            pass

                        output = await result.get_output()
                        if first_response_at is None:
                            first_response_at = time.perf_counter()
                        self._record_llm_run_observability(
                            active_deps=active_deps,
                            session_id=session_id,
                            result=result,
                            llm_started_at=llm_started_at,
                            first_response_at=first_response_at,
                            phase="agent_loop_stream",
                        )
                        serialized_history = result.all_messages_json().decode("utf-8")
            else:
                with capture_run_messages() as captured_messages_ctx:
                    captured_messages = captured_messages_ctx
                    llm_started_at = time.perf_counter()
                    result = await stream_agent.run(
                        user_prompt=stream_user_prompt,
                        deps=active_deps,
                        message_history=stream_message_history,
                        deferred_tool_results=stream_deferred_tool_results,
                    )

                self._record_llm_run_observability(
                    active_deps=active_deps,
                    session_id=session_id,
                    result=result,
                    llm_started_at=llm_started_at,
                    first_response_at=first_response_at,
                    phase="agent_loop_stream_fallback",
                )
                output = result.output
                serialized_history = result.all_messages_json().decode("utf-8")

            if repair_render_state is not None:
                output, serialized_history = await self._maybe_retry_repair_render_output(
                    active_deps=active_deps,
                    repair_renderer_agent=repair_renderer_agent,
                    render_state=repair_render_state,
                    output=output,
                    serialized_history=serialized_history,
                )

            response, full_content = self._finalize_stream_run_result(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                message_history=stream_message_history,
                serialized_history=serialized_history,
                output=output,
                render_state=repair_render_state,
            )
            if response is None:
                yield AgentRuntimeEvent(
                    type=AgentEventType.ERROR,
                    session_id=session_id,
                    message="The runtime returned an unsupported deferred tool request.",
                    metadata={"request_id": request_id},
                )
                return

            if buffer_text_deltas and full_content:
                yield AgentRuntimeEvent(
                    type=AgentEventType.TEXT_DELTA,
                    session_id=session_id,
                    content=full_content,
                    metadata={"request_id": request_id},
                )

            yield AgentRuntimeEvent(
                type=AgentEventType.DONE,
                session_id=session_id,
                metadata={
                    "request_id": request_id,
                    "response": response.model_dump(mode="json"),
                    "full_content": full_content,
                },
            )
        except LoopGuardExceededError as exc:
            convergence = self._handle_guard_exceeded(
                exc=exc,
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=status.version,
                message_history=message_history,
                captured_messages=captured_messages,
            )
            yield AgentRuntimeEvent(
                type=AgentEventType.DONE,
                session_id=session_id,
                metadata={
                    "request_id": request_id,
                    "response": convergence.response.model_dump(mode="json"),
                    "full_content": self._response_stream_full_content(convergence.response),
                    "convergence_mode": convergence.mode,
                },
            )
        except Exception as exc:
            yield AgentRuntimeEvent(
                type=AgentEventType.ERROR,
                session_id=session_id,
                message=self._public_runtime_error_message(exc),
                metadata={"request_id": request_id, "detail": str(exc)},
            )
        finally:
            self._active_streams.pop(session_id, None)

    def handle_stream_abort(self, session_id: str, partial_content: str) -> bool:
        serialized_history = self._deps.message_history_store.load_serialized_history(session_id)
        active_stream = self._active_streams.pop(session_id, None)
        if active_stream is None and not serialized_history:
            return False

        if active_stream is not None:
            messages = list(active_stream.message_history or [])
            if active_stream.user_prompt:
                from pydantic_ai.messages import ModelRequest

                messages.append(ModelRequest.user_text_prompt(active_stream.user_prompt))
        else:
            messages = list(self._deserialize_history(serialized_history))

        if partial_content.strip():
            from pydantic_ai.messages import ModelResponse, TextPart

            messages.append(ModelResponse(parts=[TextPart(content=partial_content)]))

        if messages:
            self._deps.message_history_store.save_serialized_history(
                session_id,
                self._serialize_history(messages),
            )
        return True

    def _try_extract_structured_response(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        messages: Sequence[Any],
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        for_convergence: bool = False,
    ) -> ChatResponse | None:
        requested_business = self._suggest_requested_business(request)
        tool_businesses = self._collect_tool_businesses(messages)
        has_text_response = self._has_substantive_text_response(messages)

        if (
            (
                requested_business == "PARAM_QUERY"
                and self._is_single_business(tool_businesses, "PARAM_QUERY")
            )
            or (
                requested_business is None
                and not has_text_response
                and self._is_single_business(tool_businesses, "PARAM_QUERY")
            )
        ):
            return self._try_extract_param_response(
                active_deps=active_deps,
                messages=messages,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                for_convergence=for_convergence,
            )

        if (
            (
                requested_business == "FAULT_DIAGNOSIS"
                and self._is_single_business(tool_businesses, "FAULT_DIAGNOSIS")
            )
            or (
                requested_business is None
                and not has_text_response
                and self._is_single_business(tool_businesses, "FAULT_DIAGNOSIS")
            )
        ):
            return self._try_extract_fault_diagnosis_response(
                active_deps=active_deps,
                messages=messages,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
            )

        return None

    def _resolve_doc_search_workflow_state(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
    ) -> DocSearchWorkflowRunState | None:
        if request.ask_user_answer is not None:
            if not request.session_id:
                return None
            deferred_state = active_deps.deferred_state_store.load(
                session_id=session_id,
                tool_call_id=request.ask_user_answer.tool_call_id,
            )
            if deferred_state is None or deferred_state.tool_name != DOC_SEARCH_DEFERRED_TOOL_NAME:
                return None
            query = str(deferred_state.payload.get("query") or "").strip()
            if not query:
                return None
            clarify_round = int(deferred_state.payload.get("clarify_round") or 0)
            return DocSearchWorkflowRunState(
                query=query,
                original_query=query,
                clarify_round=clarify_round,
                deferred_state=deferred_state,
            )

        if self._suggest_requested_business(request) != "DOC_SEARCH":
            return None

        query = (request.message or "").strip()
        has_image_evidence = request.ask_user_answer is None and self._has_request_or_case_image_evidence(
            request=request,
            active_deps=active_deps,
        )
        if has_image_evidence:
            query = self._build_query_with_image_evidence(query, active_deps.case_context)
        if not query and not has_image_evidence:
            return None
        return DocSearchWorkflowRunState(query=query, original_query=query)

    async def _process_doc_search_workflow(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        workflow_state: DocSearchWorkflowRunState,
    ) -> ChatResponse:
        active_deps.tracer.trace(
            event_type="doc_search_workflow_start",
            session_id=session_id,
            payload={
                "query": workflow_state.query,
                "resume": request.ask_user_answer is not None,
                "clarify_round": workflow_state.clarify_round,
            },
        )
        if request.ask_user_answer is not None:
            self._record_case_context_user_answer(active_deps=active_deps, answer=request.ask_user_answer)
        response = await self._execute_doc_search_workflow(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            workflow_state=workflow_state,
        )
        self._persist_case_context_after_doc_search(
            active_deps=active_deps,
            request=request,
            response=response,
        )
        return response

    def _resolve_parameter_query_workflow_state(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
    ) -> ParameterQueryWorkflowRunState | None:
        if request.ask_user_answer is not None:
            if not request.session_id:
                return None

            deferred_state = active_deps.deferred_state_store.load(
                session_id=session_id,
                tool_call_id=request.ask_user_answer.tool_call_id,
            )
            if deferred_state is None or deferred_state.tool_name != PARAM_QUERY_DEFERRED_TOOL_NAME:
                return None

            query = str(deferred_state.payload.get("query") or "").strip()
            if not query:
                return None
            return ParameterQueryWorkflowRunState(query=query, deferred_state=deferred_state)

        query = (request.message or "").strip()
        pending_action = getattr(active_deps.case_context, "pending_action", None)
        if query and pending_action is not None and getattr(pending_action, "business", None) == "PARAM_QUERY":
            pending_context = getattr(pending_action, "context", None) or {}
            pending_query = str(pending_context.get("query") or "").strip()
            clarify_type = str(pending_context.get("clarify_type") or "").strip().lower()
            if pending_query:
                if clarify_type == "row":
                    return ParameterQueryWorkflowRunState(query=f"{pending_query} {query}".strip())
                return ParameterQueryWorkflowRunState(query=f"{query} {pending_query}".strip())
            return ParameterQueryWorkflowRunState(query=query)

        if self._suggest_requested_business(request) != "PARAM_QUERY":
            return None

        query = self._build_query_with_image_evidence(query, active_deps.case_context)
        if not query:
            return None
        return ParameterQueryWorkflowRunState(query=query)

    async def _process_parameter_query_workflow(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        workflow_state: ParameterQueryWorkflowRunState,
    ) -> ChatResponse:
        active_deps.tracer.trace(
            event_type="parameter_query_workflow_start",
            session_id=session_id,
            payload={"query": workflow_state.query, "resume": True},
        )
        if request.ask_user_answer is not None:
            self._record_case_context_user_answer(active_deps=active_deps, answer=request.ask_user_answer)
        return await self._execute_parameter_query_workflow(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            workflow_state=workflow_state,
        )

    async def _stream_parameter_query_workflow(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        workflow_state: ParameterQueryWorkflowRunState,
    ) -> AsyncIterator[AgentRuntimeEvent]:
        yield AgentRuntimeEvent(
            type=AgentEventType.START,
            session_id=session_id,
            metadata={"request_id": request_id},
        )
        yield AgentRuntimeEvent(
            type=AgentEventType.HINT,
            session_id=session_id,
            message="正在确认参数资料，请稍候...",
            metadata={"request_id": request_id},
        )
        if request.ask_user_answer is not None:
            self._record_case_context_user_answer(active_deps=active_deps, answer=request.ask_user_answer)
        response = await self._execute_parameter_query_workflow(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            workflow_state=workflow_state,
        )
        yield AgentRuntimeEvent(
            type=AgentEventType.DONE,
            session_id=session_id,
            metadata={
                "request_id": request_id,
                "response": response.model_dump(mode="json"),
                "full_content": self._response_stream_full_content(response),
            },
        )

    async def _execute_parameter_query_workflow(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        workflow_state: ParameterQueryWorkflowRunState,
    ) -> ChatResponse:
        query = workflow_state.query
        selection_payload = None
        if request.ask_user_answer is not None:
            if workflow_state.deferred_state is None:
                return self._error_response(
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    error_code="PARAM_QUERY_RESUME_REQUIRED",
                    message="参数查询恢复态缺少 deferred_state。",
                )
            selection_payload = ParameterQueryResponseAdapter.resolve_selection_payload(
                request.ask_user_answer,
                workflow_state.deferred_state,
            )
            if selection_payload is None:
                answer_text = ParameterQueryResponseAdapter.resolve_query_hint(request.ask_user_answer)
                if answer_text:
                    query = f"{answer_text} {query}".strip()

        effective_query = CaseContextManager.build_parameter_query_with_context(active_deps.case_context, query)
        effective_selection_payload = CaseContextManager.build_parameter_selection_payload(
            active_deps.case_context,
            selection_payload,
        )
        tool_args = {
            "query": effective_query,
            "selection_payload": effective_selection_payload or {},
        }
        AgentFactory._guard_tool_call(active_deps, "query_parameters", tool_args)

        service = active_deps.parameter_query_service
        if service is None:
            envelope = {
                "status": "failed",
                "data": {"message": "parameter_query_service is unavailable."},
            }
        else:
            envelope = await service.query_async(
                query=effective_query,
                selection_payload=effective_selection_payload,
                raw_query=query,
            )
        AgentFactory._record_tool_result(active_deps, "query_parameters", tool_args, envelope)

        ask_user: AskUserQuestion | None = None
        status = str(envelope.get("status") or "").strip().lower()
        if status == "need_clarify":
            ask_user = ParameterQueryResponseAdapter.build_ask_user_question(envelope)
            active_deps.deferred_state_store.save(
                session_id=session_id,
                state=ParameterQueryResponseAdapter.build_deferred_state(
                    tool_call_id=ask_user.tool_call_id,
                    message_history_json="[]",
                    query=workflow_state.query,
                    ask_user=ask_user,
                ),
            )

        self._persist_case_context_after_parameter_query(
            active_deps=active_deps,
            query=effective_query,
            selection_payload=effective_selection_payload,
            envelope=envelope,
            ask_user=ask_user,
        )

        if status == "need_clarify" and ask_user is not None:
            return self._build_ask_user_response(
                ask_user=ask_user,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="PARAM_QUERY",
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        if status == "failed":
            data = envelope.get("data") or {}
            return self._error_response(
                deps=active_deps,
                request_id=request_id,
                session_id=session_id,
                error_code="PARAM_QUERY_FAILED",
                message=data.get("message", "参数查询失败。"),
                detail=data.get("reason"),
            )

        data = envelope.get("data") or {}
        if not data.get("matched"):
            return self._build_message_response(
                content=data.get("message") or "暂无相关参数资料。",
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="PARAM_QUERY",
                extra_metadata={
                    "parameter_query": {
                        "reason": data.get("reason"),
                        "selected_source": data.get("selected_source"),
                        "source_refs": data.get("source_refs") or [],
                    }
                },
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        return ChatResponse(
            type="param_request",
            content=ParameterQueryResponseAdapter.build_param_request_content(data),
            session_id=session_id,
            request_id=request_id,
            business="PARAM_QUERY",
            need_clarify=False,
            clarify_options=[],
            clarify_facet=None,
            metadata=self._merge_response_metadata(
                base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
                llm_observability=getattr(active_deps, "llm_observability", None),
            ),
        )

    async def _stream_doc_search_workflow(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        workflow_state: DocSearchWorkflowRunState,
    ) -> AsyncIterator[AgentRuntimeEvent]:
        yield AgentRuntimeEvent(
            type=AgentEventType.START,
            session_id=session_id,
            metadata={"request_id": request_id},
        )
        yield AgentRuntimeEvent(
            type=AgentEventType.HINT,
            session_id=session_id,
            message="正在搜索资料，请稍候...",
            metadata={"request_id": request_id},
        )
        if request.ask_user_answer is not None:
            self._record_case_context_user_answer(active_deps=active_deps, answer=request.ask_user_answer)
        progress_queue: asyncio.Queue[str] = asyncio.Queue()

        async def _progress_callback(message: str) -> None:
            normalized = str(message or "").strip()
            if normalized:
                await progress_queue.put(normalized)

        response_task = asyncio.create_task(
            self._execute_doc_search_workflow(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                workflow_state=workflow_state,
                progress_callback=_progress_callback,
            )
        )
        try:
            while not response_task.done():
                progress_task = asyncio.create_task(progress_queue.get())
                done, pending = await asyncio.wait(
                    {response_task, progress_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if progress_task in done:
                    yield AgentRuntimeEvent(
                        type=AgentEventType.HINT,
                        session_id=session_id,
                        message=progress_task.result(),
                        metadata={"request_id": request_id},
                    )
                else:
                    progress_task.cancel()
                for task in pending:
                    if task is not response_task:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

            response = await response_task
            while not progress_queue.empty():
                yield AgentRuntimeEvent(
                    type=AgentEventType.HINT,
                    session_id=session_id,
                    message=progress_queue.get_nowait(),
                    metadata={"request_id": request_id},
                )
        except Exception:
            if not response_task.done():
                response_task.cancel()
            raise
        self._persist_case_context_after_doc_search(
            active_deps=active_deps,
            request=request,
            response=response,
        )
        yield AgentRuntimeEvent(
            type=AgentEventType.DONE,
            session_id=session_id,
            metadata={
                "request_id": request_id,
                "response": response.model_dump(mode="json"),
                "full_content": self._response_stream_full_content(response),
            },
        )

    async def _execute_doc_search_workflow(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        workflow_state: DocSearchWorkflowRunState,
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResponse:
        adapter = LegacyDocSearchAdapter(active_deps)

        selection_payload = None
        search_snapshot = None
        if request.ask_user_answer is not None:
            if workflow_state.deferred_state is None:
                return self._error_response(
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    error_code="DEFERRED_TOOL_STATE_NOT_FOUND",
                    message="Deferred doc_search state was not found for this session.",
                    detail=request.ask_user_answer.tool_call_id,
                )
            selection_payload = DocSearchResponseAdapter.resolve_selection_payload(
                request.ask_user_answer,
                workflow_state.deferred_state,
            )
            search_snapshot = DocSearchResponseAdapter.resolve_search_snapshot(
                workflow_state.deferred_state,
            )
            if not isinstance(selection_payload, dict) or not selection_payload:
                return self._error_response(
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    error_code="DOC_SEARCH_SELECTION_REQUIRED",
                    message="请选择一个资料筛选项后继续。",
                )

        if request.ask_user_answer is not None and search_snapshot is not None:
            search_envelope = await adapter.search_from_snapshot(
                query=workflow_state.query,
                snapshot=search_snapshot,
                selection_payload=selection_payload,
            )
            if search_envelope.get("status") == "ok" and isinstance(search_envelope.get("data"), dict):
                data = search_envelope["data"]
                for key in (
                    "body_keyword",
                    "circuit_body_keyword",
                    "planned_queries",
                    "query_plan_rationale",
                ):
                    value = search_snapshot.get(key)
                    if value:
                        data.setdefault(key, value)
        else:
            planned_search = await self._execute_planned_doc_search(
                adapter=adapter,
                request=request,
                active_deps=active_deps,
                workflow_state=workflow_state,
                selection_payload=selection_payload,
            )
            search_envelope = planned_search.envelope

        if search_envelope.get("status") != "ok":
            response = self._build_documents_response_from_envelope(
                active_deps=active_deps,
                search_envelope=search_envelope,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
            )
            return response or self._error_response(
                deps=active_deps,
                request_id=request_id,
                session_id=session_id,
                error_code="DOC_SEARCH_FAILED",
                message="资料搜索失败。",
            )

        search_data = search_envelope.get("data") or {}
        validity = search_data.get("validity") or {}
        clarify_round = workflow_state.clarify_round + 1
        analysis_envelope = await adapter.analyze_ambiguity(
            results=search_data.get("results") or [],
            preprocessing=search_data.get("preprocessing"),
            existing_filters=search_data.get("applied_filters") or {},
            query=search_data.get("original_query") or workflow_state.query,
            validity=search_data.get("validity"),
            clarify_round=clarify_round,
            user_has_structured_selection=bool(selection_payload),
        )
        if analysis_envelope.get("status") == "need_clarify":
            ask_user = DocSearchResponseAdapter.build_ask_user_question(analysis_envelope)
            active_deps.deferred_state_store.save(
                session_id=session_id,
                state=DocSearchResponseAdapter.build_deferred_state(
                    tool_call_id=ask_user.tool_call_id,
                    message_history_json="[]",
                    query=workflow_state.query,
                    clarify_round=clarify_round,
                    ask_user=ask_user,
                    search_snapshot=search_data,
                ),
            )
            return self._build_ask_user_response(
                ask_user=ask_user,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="DOC_SEARCH",
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        if validity.get("has_valid_results") is False:
            response = self._build_documents_response_from_envelope(
                active_deps=active_deps,
                search_envelope=search_envelope,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
            )
            return response or self._build_message_response(
                content=DocSearchResponseAdapter.build_invalid_message_content(search_data),
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="DOC_SEARCH",
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        if progress_callback is not None and self._can_attempt_circuit_body_search(
            active_deps=active_deps,
            search_data=search_data,
            workflow_state=workflow_state,
        ):
            await progress_callback("正在电路图内搜索定位，请稍候...")

        await self._enhance_doc_search_with_circuit_body_search(
            active_deps=active_deps,
            search_data=search_data,
            workflow_state=workflow_state,
        )

        response = self._build_documents_response_from_envelope(
            active_deps=active_deps,
            search_envelope=search_envelope,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
        )
        if response is not None:
            return response

        return self._error_response(
            deps=active_deps,
            request_id=request_id,
            session_id=session_id,
            error_code="DOC_SEARCH_RESPONSE_BUILD_FAILED",
            message="资料搜索结果组装失败。",
        )

    @staticmethod
    def _is_circuit_body_search_candidate_result(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        value = item.get("ggzj_data_type")
        if isinstance(value, int):
            if value == 3:
                return True
        text = str(value or "").strip()
        if text.isdigit() and int(text) == 3:
            return True

        text_parts: list[str] = []
        for key in ("filename", "title", "physical_path", "hierarchy_full", "ggzj_file_type", "file_type"):
            field_value = item.get(key)
            if field_value:
                text_parts.append(str(field_value))
        for key in ("doc_types", "tags"):
            field_value = item.get(key)
            if isinstance(field_value, dict):
                text_parts.extend(str(part) for part in field_value.values() if part)
            elif isinstance(field_value, (list, tuple, set)):
                text_parts.extend(str(part) for part in field_value if part)
            elif field_value:
                text_parts.append(str(field_value))

        joined = " ".join(text_parts)
        return any(marker in joined for marker in ("电路图", "线束图", "针脚定义", "针脚图"))

    @classmethod
    def _has_circuit_body_search_candidate(cls, search_data: dict[str, Any]) -> bool:
        return any(
            cls._is_circuit_body_search_candidate_result(item)
            for item in (search_data.get("results") or [])
        )

    @staticmethod
    def _can_attempt_circuit_body_search(
        *,
        active_deps: AgentRuntimeDeps,
        search_data: dict[str, Any],
        workflow_state: DocSearchWorkflowRunState,
    ) -> bool:
        if getattr(active_deps, "circuit_body_search_enhancer", None) is None:
            return False
        if not search_data.get("results"):
            return False
        if not AgentLoopService._has_circuit_body_search_candidate(search_data):
            return False
        keyword = resolve_circuit_body_keyword(
            search_data=search_data,
            fallback_query=workflow_state.query,
        )
        return bool(keyword)

    async def _enhance_doc_search_with_circuit_body_search(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        search_data: dict[str, Any],
        workflow_state: DocSearchWorkflowRunState,
    ) -> None:
        tracer = getattr(active_deps, "tracer", None)
        session_id = getattr(active_deps, "request_session_id", None)

        def trace_circuit_event(
            event_type: str,
            payload: dict[str, Any],
            detail: str | None = None,
        ) -> None:
            if tracer is None:
                return
            tracer.trace(
                event_type=event_type,
                session_id=session_id,
                detail=detail,
                payload=payload,
            )

        enhancer = getattr(active_deps, "circuit_body_search_enhancer", None)
        if enhancer is None:
            trace_circuit_event(
                "circuit_body_search_skipped",
                {
                    "reason": "enhancer_unavailable",
                    "workflow_query": workflow_state.query,
                    "source_result_count": len(search_data.get("results") or []),
                },
            )
            return

        keyword = resolve_circuit_body_keyword(
            search_data=search_data,
            fallback_query=workflow_state.query,
        )
        if not keyword:
            trace_circuit_event(
                "circuit_body_search_skipped",
                {
                    "reason": "missing_keyword",
                    "workflow_query": workflow_state.query,
                    "source_result_count": len(search_data.get("results") or []),
                    "candidate_query": (
                        str(search_data.get("query") or "").strip()
                        or str(search_data.get("original_query") or "").strip()
                        or workflow_state.query
                    ),
                },
            )
            return

        if not self._has_circuit_body_search_candidate(search_data):
            trace_circuit_event(
                "circuit_body_search_skipped",
                {
                    "reason": "missing_circuit_data_type_results",
                    "workflow_query": workflow_state.query,
                    "source_result_count": len(search_data.get("results") or []),
                    "candidate_query": (
                        str(search_data.get("query") or "").strip()
                        or str(search_data.get("original_query") or "").strip()
                        or workflow_state.query
                    ),
                },
            )
            return

        candidate_query = (
            str(search_data.get("query") or "").strip()
            or str(search_data.get("original_query") or "").strip()
            or workflow_state.query
        )
        source_result_count = len(search_data.get("results") or [])
        try:
            circuit_body_max_docs = len(search_data.get("results") or [])
            trace_circuit_event(
                "circuit_body_search_started",
                {
                    "keyword": keyword,
                    "candidate_query": candidate_query,
                    "workflow_query": workflow_state.query,
                    "source_result_count": source_result_count,
                    "max_docs": circuit_body_max_docs,
                    "max_candidate_docs": 20,
                },
            )
            search_data["results"] = await enhancer.enhance(
                results=search_data.get("results") or [],
                body_keyword=keyword,
                max_docs=circuit_body_max_docs,
                candidate_query=candidate_query,
                max_candidate_docs=20,
                trace_callback=trace_circuit_event,
            )
            if tracer is not None:
                body_hit_count = sum(
                    1
                    for item in search_data.get("results") or []
                    if isinstance(item, dict) and (item.get("body_search") or {}).get("status") == "hit"
                )
                trace_circuit_event(
                    "circuit_body_search_enhanced",
                    {
                        "keyword": keyword,
                        "candidate_query": candidate_query,
                        "source_result_count": source_result_count,
                        "result_count": len(search_data.get("results") or []),
                        "body_hit_count": body_hit_count,
                    },
                )
        except Exception as exc:
            trace_circuit_event(
                "circuit_body_search_enhance_failed",
                {
                    "keyword": keyword,
                    "candidate_query": candidate_query,
                    "workflow_query": workflow_state.query,
                    "source_result_count": source_result_count,
                    "result_count": len(search_data.get("results") or []),
                },
                detail=str(exc),
            )

    @staticmethod
    def _current_run_messages(
        *,
        full_messages: Sequence[Any],
        message_history: Sequence[Any] | None,
    ) -> Sequence[Any]:
        history_len = len(message_history or [])
        if history_len <= 0:
            return full_messages
        return full_messages[history_len:]

    def _build_documents_response_from_envelope(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        search_envelope: dict[str, Any] | None,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse | None:
        if search_envelope is None:
            return None

        if search_envelope.get("status") != "ok":
            error_data = search_envelope.get("data") or {}
            error_code = error_data.get("error_code")
            if error_code in {"TOKEN_REQUIRED", "TOKEN_EXPIRED"}:
                return self._error_response(
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    error_code=error_code,
                    message=error_data.get("message", "未登录，请重新进入"),
                    detail=error_data.get("reason"),
                )
            return None

        search_data = search_envelope.get("data") or {}
        if not search_data:
            return None

        validity = search_data.get("validity") or {}
        if validity.get("has_valid_results") is False:
            return ChatResponse(
                type="message",
                content=DocSearchResponseAdapter.build_invalid_message_content(search_data),
                session_id=session_id,
                request_id=request_id,
                business="DOC_SEARCH",
                need_clarify=False,
                clarify_options=[],
                clarify_facet=None,
                result_summary=search_data.get("result_summary"),
                metadata=self._merge_response_metadata(
                    base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
                    llm_observability=getattr(active_deps, "llm_observability", None),
                ),
            )

        return ChatResponse(
            type="documents",
            content=DocSearchResponseAdapter.build_documents_content(search_data),
            session_id=session_id,
            request_id=request_id,
            business="DOC_SEARCH",
            need_clarify=False,
            clarify_options=[],
            clarify_facet=None,
            result_summary=search_data.get("result_summary"),
            metadata=self._merge_response_metadata(
                base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
                llm_observability=getattr(active_deps, "llm_observability", None),
            ),
        )

    def _try_extract_param_response(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        messages: Sequence[Any],
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        for_convergence: bool = False,
    ) -> ChatResponse | None:
        parameter_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "query_parameters")
        if parameter_envelope is None:
            return None

        status = parameter_envelope.get("status")
        if status == "failed":
            error_data = parameter_envelope.get("data") or {}
            if for_convergence:
                return self._build_message_response(
                    content=error_data.get("message", "参数查询失败。"),
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business="PARAM_QUERY",
                    extra_metadata={
                        "parameter_query": {
                            "reason": error_data.get("reason"),
                        }
                    },
                    llm_observability=getattr(active_deps, "llm_observability", None),
                )
            return self._error_response(
                deps=active_deps,
                request_id=request_id,
                session_id=session_id,
                error_code="PARAM_QUERY_FAILED",
                message=error_data.get("message", "参数查询失败。"),
                detail=error_data.get("reason"),
            )

        if status == "need_clarify" and for_convergence:
            return None

        if status != "ok":
            return None

        data = parameter_envelope.get("data") or {}
        if not data:
            return None

        if not data.get("matched"):
            return self._build_message_response(
                content=data.get("message") or "暂无相关参数资料。",
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="PARAM_QUERY",
                extra_metadata={
                    "parameter_query": {
                        "reason": data.get("reason"),
                        "selected_source": data.get("selected_source"),
                        "source_refs": data.get("source_refs") or [],
                    }
                },
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        return ChatResponse(
            type="param_request",
            content=ParameterQueryResponseAdapter.build_param_request_content(data),
            session_id=session_id,
            request_id=request_id,
            business="PARAM_QUERY",
            need_clarify=False,
            clarify_options=[],
            clarify_facet=None,
            metadata=self._merge_response_metadata(
                base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
                llm_observability=getattr(active_deps, "llm_observability", None),
            ),
        )

    def _try_recover_standalone_param_response(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        messages: Sequence[Any],
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse | None:
        if request.ask_user_answer is not None:
            return None

        if self._suggest_requested_business(request) != "PARAM_QUERY":
            return None

        if self._collect_tool_businesses(messages):
            return None

        query = (request.message or "").strip()
        if not query:
            return None

        service = active_deps.parameter_query_service
        if service is None:
            return None

        try:
            envelope = service.query(query=query, raw_query=query)
        except Exception:
            return None

        if envelope.get("status") != "ok":
            return None

        data = envelope.get("data") or {}
        if not data.get("matched"):
            return None

        return ChatResponse(
            type="param_request",
            content=ParameterQueryResponseAdapter.build_param_request_content(data),
            session_id=session_id,
            request_id=request_id,
            business="PARAM_QUERY",
            need_clarify=False,
            clarify_options=[],
            clarify_facet=None,
            metadata=self._merge_response_metadata(
                base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
                llm_observability=getattr(active_deps, "llm_observability", None),
                extra={"recovered_without_tool_call": True},
            ),
        )

    def _try_extract_fault_diagnosis_response(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        messages: Sequence[Any],
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse | None:
        diagnosis_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "dtc_diagnosis")
        if diagnosis_envelope is not None:
            status = str(diagnosis_envelope.get("status") or "").strip().lower()
            if status == "need_clarify":
                return self._build_ask_user_response(
                    ask_user=self._build_ask_user_from_clarify_envelope(
                        clarify_envelope=diagnosis_envelope,
                        tool_call_prefix="fault_diag",
                        default_question="请选择对应 ECU",
                    ),
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business="FAULT_DIAGNOSIS",
                    llm_observability=getattr(active_deps, "llm_observability", None),
                )
            if status == "failed":
                return self._build_fault_diagnosis_message_response(
                    active_deps=active_deps,
                    envelope=diagnosis_envelope,
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=runtime_version,
                )
            return None

        lookup_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "lookup_ecu_candidates")
        if lookup_envelope is not None:
            status = str(lookup_envelope.get("status") or "").strip().lower()
            if status == "need_clarify":
                return self._build_ask_user_response(
                    ask_user=self._build_ask_user_from_clarify_envelope(
                        clarify_envelope=lookup_envelope,
                        tool_call_prefix="fault_diag",
                        default_question="请选择对应 ECU",
                    ),
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business="FAULT_DIAGNOSIS",
                    llm_observability=getattr(active_deps, "llm_observability", None),
                )
            if status == "failed":
                return self._build_fault_diagnosis_message_response(
                    active_deps=active_deps,
                    envelope=lookup_envelope,
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=runtime_version,
                )

        return None

    @staticmethod
    def _tool_name_to_business(tool_name: str) -> str | None:
        if tool_name == "query_parameters":
            return "PARAM_QUERY"
        if tool_name in {"lookup_ecu_candidates", "dtc_diagnosis"}:
            return "FAULT_DIAGNOSIS"
        if tool_name in {"lookup_repair_knowledge_titles", "get_repair_knowledge_context"}:
            return "GENERAL_CHAT"
        return None

    def _collect_tool_businesses(self, messages: Sequence[Any]) -> set[str]:
        from pydantic_ai.messages import ModelRequest, ToolReturnPart

        businesses: set[str] = set()
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, ToolReturnPart):
                    continue
                business = self._tool_name_to_business(part.tool_name)
                if business is not None:
                    businesses.add(business)
        return businesses

    def _extract_latest_tool_business(self, messages: Sequence[Any] | None) -> str | None:
        if not messages:
            return None

        from pydantic_ai.messages import ModelRequest, ToolReturnPart

        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue
            for part in reversed(message.parts):
                if not isinstance(part, ToolReturnPart):
                    continue
                business = self._tool_name_to_business(part.tool_name)
                if business is not None:
                    return business
        return None

    @staticmethod
    def _is_single_business(businesses: set[str], business: str) -> bool:
        return bool(businesses) and businesses == {business}

    @staticmethod
    def _has_substantive_text_response(messages: Sequence[Any] | None) -> bool:
        if not messages:
            return False

        from pydantic_ai.messages import ModelResponse, TextPart

        for message in reversed(messages):
            if not isinstance(message, ModelResponse):
                continue
            for part in message.parts:
                if isinstance(part, TextPart) and str(part.content or "").strip():
                    return True
        return False

    def _suggest_requested_business(self, request: ChatRequest) -> str | None:
        normalized_mode = (request.mode or "auto").strip().lower()
        mode_map = {
            "doc_search": "DOC_SEARCH",
            "param_query": "PARAM_QUERY",
            "fault_diagnosis": "FAULT_DIAGNOSIS",
            "general_chat": "GENERAL_CHAT",
        }
        if normalized_mode in mode_map:
            return mode_map[normalized_mode]

        context = request.context if isinstance(request.context, dict) else {}
        resume_business = str(context.get(self._RESUME_BUSINESS_CONTEXT_KEY) or "").strip().upper()
        if resume_business in {"DOC_SEARCH", "PARAM_QUERY", "FAULT_DIAGNOSIS", "GENERAL_CHAT"}:
            return resume_business

        message = (request.message or "").strip()
        image_evidence_business = self._infer_business_from_image_evidence_payloads(
            self._extract_request_image_evidence_payloads(request)
        )
        if not message:
            return image_evidence_business

        router = RequestIntentRouter(
            fault_code_parser=self._deps.fault_code_parser,
            diagnosis_enabled_provider=self._is_diagnosis_enabled,
            config_service=self._deps.config_service,
        )
        router_text = self._build_intent_router_text_from_request_context(request) or message
        high_confidence = router.route_high_confidence(router_text)
        if high_confidence is not None:
            if high_confidence.intent == RoutedIntent.DOC_SEARCH:
                return "DOC_SEARCH"
            if high_confidence.intent == RoutedIntent.PARAM_QUERY:
                return "PARAM_QUERY"
            if high_confidence.intent in {RoutedIntent.FAULT_DIAGNOSIS, RoutedIntent.FAULT_DIAGNOSIS_LLM}:
                return "FAULT_DIAGNOSIS"
            if high_confidence.intent == RoutedIntent.GENERAL_CHAT:
                return "GENERAL_CHAT"

        cached = self._cached_intent_decision(request)
        if cached is not None:
            if cached.intent == RoutedIntent.DOC_SEARCH:
                return "DOC_SEARCH"
            if cached.intent == RoutedIntent.PARAM_QUERY:
                return "PARAM_QUERY"
            if cached.intent in {RoutedIntent.FAULT_DIAGNOSIS, RoutedIntent.FAULT_DIAGNOSIS_LLM}:
                return "FAULT_DIAGNOSIS"
            if cached.intent == RoutedIntent.GENERAL_CHAT:
                return "GENERAL_CHAT"
            return None

        decision = router.route(router_text, request.mode)
        if decision.intent == RoutedIntent.DOC_SEARCH:
            return "DOC_SEARCH"
        if decision.intent == RoutedIntent.PARAM_QUERY:
            return "PARAM_QUERY"
        if decision.intent in {RoutedIntent.FAULT_DIAGNOSIS, RoutedIntent.FAULT_DIAGNOSIS_LLM}:
            return "FAULT_DIAGNOSIS"
        if decision.intent == RoutedIntent.GENERAL_CHAT:
            return "GENERAL_CHAT"
        return None

    def _infer_business_from_messages(
        self,
        messages: Sequence[Any] | None,
        request: ChatRequest,
        *,
        fallback_messages: Sequence[Any] | None = None,
    ) -> str:
        business = self._extract_latest_tool_business(messages)
        if business is not None:
            return business

        business = self._extract_latest_tool_business(fallback_messages)
        if business is not None:
            return business

        suggested = self._suggest_requested_business(request)
        if suggested is not None:
            return suggested

        if request.ask_user_answer is not None:
            return "AGENT_LOOP"
        return "GENERAL_CHAT"

    def _infer_message_business(
        self,
        messages: Sequence[Any] | None,
        request: ChatRequest,
        *,
        fallback_messages: Sequence[Any] | None = None,
    ) -> str:
        suggested = self._suggest_requested_business(request)
        if suggested in {"GENERAL_CHAT", "FAULT_DIAGNOSIS"}:
            return suggested

        return self._infer_business_from_messages(
            messages,
            request,
            fallback_messages=fallback_messages,
        )

    @staticmethod
    def _extract_latest_user_prompt(messages: Sequence[Any] | None) -> str | None:
        if not messages:
            return None

        from pydantic_ai.messages import ModelRequest, UserPromptPart

        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    prompt = part.content.strip()
                    if prompt:
                        return prompt
        return None

    def _is_diagnosis_enabled(self) -> bool:
        if self._deps.config_service is not None:
            return bool(self._deps.config_service.get("diagnosis_service_enabled", False))
        return False

    @staticmethod
    def _get_runtime_config(runtime_deps: AgentRuntimeDeps, key: str, default: Any) -> Any:
        config_service = getattr(runtime_deps, "config_service", None)
        if config_service is None:
            return default
        return config_service.get(key, default)

    def _build_repair_knowledge_service(self, runtime_deps: AgentRuntimeDeps) -> Any:
        existing_service = getattr(runtime_deps, "repair_knowledge_service", None)
        if existing_service is not None:
            return existing_service
        try:
            from app.agent.domain.repair_knowledge import RepairKnowledgeService

            path = self._get_runtime_config(
                runtime_deps,
                "repair_knowledge_path",
                settings.repair_knowledge_path,
            )
            return RepairKnowledgeService(path)
        except Exception:
            return existing_service

    def _resolve_request_agents(
        self,
        active_deps: AgentRuntimeDeps,
    ) -> tuple[Any | None, Any | None, Any | None, Any | None]:
        if not self._status.available:
            return None, None, None, None
        try:
            agent = (
                self.__dict__["_agent"]
                if "_agent" in self.__dict__
                else self._factory.create_agent(active_deps)
            )
            if "_repair_gate_agent" in self.__dict__:
                repair_gate_agent = self.__dict__["_repair_gate_agent"]
            else:
                create_repair_gate_agent = getattr(self._factory, "create_repair_gate_agent", None)
                repair_gate_agent = (
                    create_repair_gate_agent(active_deps)
                    if callable(create_repair_gate_agent)
                    else None
                )
            if "_repair_renderer_agent" in self.__dict__:
                repair_renderer_agent = self.__dict__["_repair_renderer_agent"]
            else:
                create_repair_renderer_agent = getattr(self._factory, "create_repair_renderer_agent", None)
                repair_renderer_agent = (
                    create_repair_renderer_agent(active_deps)
                    if callable(create_repair_renderer_agent)
                    else None
                )
            if repair_renderer_agent is None:
                repair_render_planner_agent = None
            elif "_repair_render_planner_agent" in self.__dict__:
                repair_render_planner_agent = self.__dict__["_repair_render_planner_agent"]
            else:
                create_repair_render_planner_agent = getattr(self._factory, "create_repair_render_planner_agent", None)
                repair_render_planner_agent = (
                    create_repair_render_planner_agent(active_deps)
                    if callable(create_repair_render_planner_agent)
                    else None
                )
            return agent, repair_gate_agent, repair_render_planner_agent, repair_renderer_agent
        except Exception as exc:
            active_deps.tracer.trace(
                event_type="agent_loop_agent_build_failed",
                session_id=getattr(active_deps, "request_session_id", None),
                detail=str(exc),
            )
            return None, None, None, None

    def _prepare_request_runtime_deps(
        self,
        *,
        runtime_deps: AgentRuntimeDeps,
        request: ChatRequest,
        session_id: str,
    ) -> AgentRuntimeDeps:
        loop_guard = self._build_loop_guard(runtime_deps)
        case_context = None
        manager = self._get_case_context_manager(runtime_deps)
        if manager is not None:
            case_context = manager.reset(session_id) if self._should_reset_history(request) else manager.load(session_id)
            case_context = manager.attach_runtime_state(case_context, loop_guard=loop_guard)

        active_deps = runtime_deps.clone_for_request(
            request_session_id=session_id,
            case_context=case_context,
            loop_guard=loop_guard,
            runtime_tool_history=[],
            llm_observability=None,
        )
        active_deps.repair_knowledge_service = self._build_repair_knowledge_service(active_deps)
        return active_deps

    def _build_loop_guard(self, runtime_deps: AgentRuntimeDeps) -> LoopGuard:
        return LoopGuard(
            max_tool_calls=int(self._get_runtime_config(runtime_deps, "loop_guard_max_tool_calls", settings.loop_guard_max_tool_calls)),
            max_external_tool_calls=int(
                self._get_runtime_config(
                    runtime_deps,
                    "loop_guard_max_external_tool_calls",
                    settings.loop_guard_max_external_tool_calls,
                )
            ),
            max_ask_user_calls=int(
                self._get_runtime_config(
                    runtime_deps,
                    "loop_guard_max_ask_user_calls",
                    settings.loop_guard_max_ask_user_calls,
                )
            ),
            max_no_gain_streak=int(
                self._get_runtime_config(
                    runtime_deps,
                    "loop_guard_max_no_gain_streak",
                    settings.loop_guard_max_no_gain_streak,
                )
            ),
            max_same_tool_repeat=int(
                self._get_runtime_config(
                    runtime_deps,
                    "loop_guard_max_same_tool_repeat",
                    settings.loop_guard_max_same_tool_repeat,
                )
            ),
            max_same_args_repeat=int(
                self._get_runtime_config(
                    runtime_deps,
                    "loop_guard_max_same_args_repeat",
                    settings.loop_guard_max_same_args_repeat,
                )
            ),
        )

    def _build_user_prompt_with_case_context(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        request: ChatRequest,
        message_history: Sequence[Any] | None = None,
    ) -> str | None:
        user_prompt = (request.message or "").strip() or None
        if user_prompt is None and self._is_repair_followup_answer_request(request):
            user_prompt = "请基于已加载资料、共享上下文和用户刚补充的信息继续判断当前是否还需要追问。"
        if user_prompt is None:
            user_prompt = self._build_image_evidence_user_prompt(
                request=request,
                active_deps=active_deps,
                include_summary=False,
            )
        if user_prompt is None:
            return None

        context_prompt = self._build_case_context_prompt(active_deps, active_deps.case_context)
        base_prompt = user_prompt
        if context_prompt:
            base_prompt = f"{context_prompt}\n\n[CURRENT_USER_MESSAGE]\n{user_prompt}\n[/CURRENT_USER_MESSAGE]"

        decorated_prompt = self._decorate_fault_diagnosis_llm_prompt_if_needed(
            request=request,
            user_prompt=base_prompt,
        )
        return self._decorate_repair_followup_resume_prompt_if_needed(
            active_deps=active_deps,
            request=request,
            user_prompt=decorated_prompt,
            message_history=message_history,
        )

    def _decorate_repair_followup_resume_prompt_if_needed(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        request: ChatRequest,
        user_prompt: str,
        message_history: Sequence[Any] | None,
    ) -> str:
        if not self._is_repair_followup_answer_request(request):
            return user_prompt

        query_state = self._resolve_repair_followup_query_state(
            request=request,
            active_deps=active_deps,
            message_history=message_history,
        )
        followup_state = self._resolve_repair_followup_summary(
            request=request,
            active_deps=active_deps,
            message_history=message_history,
        )
        summary_text = followup_state.summary_text
        field_values = followup_state.field_values
        answered_lines: list[str] = []
        label_map = {
            "ecu_or_system": "车辆/系统信息",
            "fault_phenomenon": "当前故障现象",
            "working_condition": "故障发生工况",
            "fault_codes": "故障码情况",
            "data_evidence": "已完成的检查项",
            "repair_history": "近期维修历史",
        }
        answered_keys: list[str] = []
        for key, item in field_values.items():
            selected = [str(value).strip() for value in (item.get("selected") or []) if str(value).strip()]
            text_value = str(item.get("text") or "").strip()
            values = [*selected]
            if text_value:
                values.append(text_value)
            if not values:
                continue
            answered_keys.append(key)
            answered_lines.append(f"- {label_map.get(key, key)}：{'、'.join(values)}")

        if not answered_lines and not summary_text:
            return user_prompt

        repeated_key_line = "、".join(answered_keys) if answered_keys else "无"
        return (
            "[REPAIR_FOLLOWUP_RESUME]\n"
            "这是维修问答补充后的恢复轮次。\n"
            f"原始问题：{query_state.original_query or '无'}\n"
            "下面这些信息已经由用户明确回答过，你必须把它们视为已知条件继续推理。\n"
            "禁止再次用 ask_user_question 重复询问这些已经回答过的字段；只有在确实需要新的、不同类型的信息时，才允许继续追问。\n"
            "请基于已加载资料、共享上下文和用户刚补充的信息继续判断当前是否还需要追问。\n"
            f"已回答字段 key：{repeated_key_line}\n"
            f"用户补充摘要：{summary_text or '无'}\n"
            "已回答内容：\n"
            f"{chr(10).join(answered_lines) if answered_lines else '- 无'}\n"
            "[/REPAIR_FOLLOWUP_RESUME]\n\n"
            f"{user_prompt}"
        )

    def _decorate_fault_diagnosis_llm_prompt_if_needed(
        self,
        *,
        request: ChatRequest,
        user_prompt: str,
    ) -> str:
        message = (request.message or "").strip()
        if not message:
            return user_prompt

        decision = self._cached_intent_decision(request)
        if decision is None:
            router = RequestIntentRouter(
                fault_code_parser=self._deps.fault_code_parser,
                diagnosis_enabled_provider=self._is_diagnosis_enabled,
                config_service=self._deps.config_service,
            )
            decision = router.route(message, request.mode)
        if decision.intent != RoutedIntent.FAULT_DIAGNOSIS_LLM:
            return user_prompt

        normalized_fault_code = decision.normalized_fault_code or message
        return (
            "[FAULT_DIAGNOSIS_FALLBACK]\n"
            "当前外部故障诊断服务未接入或未启用。\n"
            "请把本轮请求当作故障码诊断问题处理，不要按普通通用问答回答。\n"
            f"已识别故障码：{normalized_fault_code}\n"
            "请仅基于通用维修知识作答，并明确不同车型、ECU 或标定版本可能存在差异。\n"
            "优先给出：故障码可能含义、常见原因、优先检查步骤、维修建议。\n"
            "不要假装已经调用外部诊断服务，也不要虚构诊断报告。\n"
            "[/FAULT_DIAGNOSIS_FALLBACK]\n\n"
            f"{user_prompt}"
        )

    def _build_case_context_prompt(self, active_deps: AgentRuntimeDeps, case_context: Any) -> str:
        if not bool(self._get_runtime_config(active_deps, "case_context_enabled", settings.case_context_enabled)) or case_context is None:
            return ""
        max_chars = int(
            self._get_runtime_config(
                active_deps,
                "case_context_prompt_max_chars",
                settings.case_context_prompt_max_chars,
            )
        )
        return CaseContextPromptBuilder(max_chars=max_chars).build(case_context)

    def _get_case_context_manager(self, active_deps: AgentRuntimeDeps) -> CaseContextManager | None:
        if not bool(self._get_runtime_config(active_deps, "case_context_enabled", settings.case_context_enabled)):
            return None
        store = getattr(active_deps, "case_context_store", None)
        if store is None:
            return None
        return CaseContextManager(
            store=store,
            max_artifacts_total=int(
                self._get_runtime_config(
                    active_deps,
                    "case_context_max_artifacts_total",
                    settings.case_context_max_artifacts_total,
                )
            ),
            max_artifacts_per_type=int(
                self._get_runtime_config(
                    active_deps,
                    "case_context_max_artifacts_per_type",
                    settings.case_context_max_artifacts_per_type,
                )
            ),
            max_selected_docs=int(
                self._get_runtime_config(
                    active_deps,
                    "case_context_max_selected_docs",
                    settings.case_context_max_selected_docs,
                )
            ),
            max_serialized_bytes=int(
                self._get_runtime_config(
                    active_deps,
                    "case_context_max_serialized_bytes",
                    settings.case_context_max_serialized_bytes,
                )
            ),
        )

    def _record_case_context_user_answer(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        answer: AskUserAnswer,
    ) -> None:
        manager = self._get_case_context_manager(active_deps)
        if manager is None or active_deps.case_context is None:
            return
        active_deps.case_context = manager.save(
            manager.attach_runtime_state(
                manager.record_user_answer(active_deps.case_context, answer),
                loop_guard=active_deps.loop_guard,
            )
        )

    def _persist_case_context_after_agent_run(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        run_messages: Sequence[Any],
        request: ChatRequest,
        ask_user: AskUserQuestion | None,
        business: str | None,
    ) -> None:
        manager = self._get_case_context_manager(active_deps)
        if manager is None or active_deps.case_context is None:
            return

        context = manager.record_run_messages(
            active_deps.case_context,
            run_messages=run_messages,
            loop_guard=active_deps.loop_guard,
        )
        if ask_user is not None:
            context = manager.record_pending_action(
                context,
                ask_user=ask_user,
                business=business or "AGENT_LOOP",
                scene=(ask_user.context or {}).get("scene") or (ask_user.context or {}).get("card_type") or "ask_user",
            )
            context = manager.attach_runtime_state(context, loop_guard=active_deps.loop_guard)
        active_deps.case_context = manager.save(context)

    def _persist_case_context_after_doc_search(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        request: ChatRequest,
        response: ChatResponse,
    ) -> None:
        manager = self._get_case_context_manager(active_deps)
        if manager is None or active_deps.case_context is None:
            return
        active_deps.case_context = manager.save(
            manager.record_doc_search_response(
                active_deps.case_context,
                request=request,
                response=response,
            )
        )

    def _persist_case_context_after_parameter_query(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        query: str,
        selection_payload: dict[str, Any] | None,
        envelope: dict[str, Any],
        ask_user: AskUserQuestion | None,
    ) -> None:
        manager = self._get_case_context_manager(active_deps)
        if manager is None or active_deps.case_context is None:
            return
        active_deps.case_context = manager.save(
            manager.record_parameter_query_envelope(
                active_deps.case_context,
                query=query,
                selection_payload=selection_payload,
                envelope=envelope,
                ask_user=ask_user,
                loop_guard=active_deps.loop_guard,
            )
        )

    def _should_use_repair_answer_gate(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        repair_gate_agent: Any | None,
        repair_renderer_agent: Any | None,
    ) -> bool:
        del repair_renderer_agent
        if repair_gate_agent is None:
            return False

        if active_deps.repair_knowledge_service is None:
            return False

        if request.ask_user_answer is not None:
            deferred_state = active_deps.deferred_state_store.load(
                session_id=session_id,
                tool_call_id=request.ask_user_answer.tool_call_id,
            )
            if deferred_state is None:
                return False
            raw_payload = deferred_state.payload if isinstance(deferred_state.payload, dict) else {}
            if raw_payload.get("synthetic_followup") is True:
                return False
            ask_user_payload = raw_payload.get("ask_user") if isinstance(raw_payload.get("ask_user"), dict) else raw_payload
            context = ask_user_payload.get("context") or {}
            return (
                context.get("scene") == "repair_knowledge_followup"
                or context.get("card_type") == "repair_followup"
            )

        if self._suggest_requested_business(request) != "GENERAL_CHAT":
            return False

        query = (request.message or "").strip()
        if not query:
            return False

        try:
            title_catalog = active_deps.repair_knowledge_service.lookup_titles(query)
        except Exception:
            return RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query(query)
        recommended_titles = ((title_catalog.get("data") or {}).get("recommended_titles") or [])
        return bool(recommended_titles) or RepairKnowledgeFollowupAdapter.is_repair_diagnosis_query(query)

    def _should_reset_repair_gate_resume_state(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
    ) -> bool:
        if not self._is_repair_followup_answer_request(request):
            return False
        answer = request.ask_user_answer
        if answer is None:
            return False
        deferred_state = active_deps.deferred_state_store.load(
            session_id=session_id,
            tool_call_id=answer.tool_call_id,
        )
        if deferred_state is None or deferred_state.tool_name != "ask_user_question":
            return False
        payload = deferred_state.payload if isinstance(deferred_state.payload, dict) else {}
        if payload.get("synthetic_followup") is True:
            return False
        resume_business = str(payload.get("resume_business") or "").strip().upper()
        if resume_business:
            return False
        return True

    async def _run_repair_answer_gate(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        message_history: Sequence[Any] | None,
        deferred_tool_results: Any,
        user_prompt: str | None,
        repair_gate_agent: Any | None,
        repair_renderer_agent: Any | None,
    ) -> RepairAnswerGateReadyState | ChatResponse | None:
        del repair_renderer_agent
        if repair_gate_agent is None:
            return None

        from pydantic_ai import DeferredToolRequests
        try:
            gate_message_history = message_history
            gate_deferred_tool_results = deferred_tool_results
            gate_user_prompt = user_prompt
            if self._should_reset_repair_gate_resume_state(
                request=request,
                active_deps=active_deps,
                session_id=session_id,
            ):
                gate_message_history = None
                gate_deferred_tool_results = None
                active_deps.tracer.trace(
                    event_type="repair_answer_gate_reset_resume_state",
                    session_id=session_id,
                    payload={"tool_call_id": request.ask_user_answer.tool_call_id if request.ask_user_answer else None},
                )

            llm_started_at = time.perf_counter()
            result = await repair_gate_agent.run(
                user_prompt=gate_user_prompt,
                deps=active_deps,
                message_history=gate_message_history,
                deferred_tool_results=gate_deferred_tool_results,
            )
            self._record_llm_run_observability(
                active_deps=active_deps,
                session_id=session_id,
                result=result,
                llm_started_at=llm_started_at,
                phase="repair_answer_gate",
            )

            serialized_history = result.all_messages_json().decode("utf-8")
            full_messages = self._deserialize_history(serialized_history)
            run_messages = self._current_run_messages(
                full_messages=full_messages,
                message_history=gate_message_history,
            )
            loaded_context = self._extract_loaded_repair_knowledge_context(full_messages)
            followup_query_state = self._resolve_repair_followup_query_state(
                request=request,
                active_deps=active_deps,
                message_history=full_messages,
            ) if self._is_repair_followup_answer_request(request) else None
            review_query = followup_query_state.evidence_query if followup_query_state is not None else (
                self._extract_latest_user_prompt(full_messages) or (request.message or "").strip()
            )
            review = await review_repair_answer_gate_async(
                query=review_query,
                loaded_context=loaded_context,
                no_gain_streak=int(getattr(active_deps.case_context, "no_gain_streak", 0) or 0),
            )

            if isinstance(result.output, DeferredToolRequests):
                ask_user = self._extract_ask_user_question(result.output)
                if ask_user is None:
                    return self._error_response(
                        deps=active_deps,
                        request_id=request_id,
                        session_id=session_id,
                        error_code="UNSUPPORTED_DEFERRED_TOOL_REQUEST",
                        message="The runtime returned a deferred tool request that is not mapped yet.",
                    )
                ask_user = await self._normalize_runtime_ask_user_question_async(
                    ask_user=ask_user,
                    request=request,
                    full_messages=full_messages,
                )

                business = self._infer_business_from_messages(
                    run_messages,
                    request,
                    fallback_messages=full_messages,
                )
                active_deps.tracer.trace(
                    event_type="repair_answer_gate_ask_user",
                    session_id=session_id,
                    payload={"tool_call_id": ask_user.tool_call_id, "question": ask_user.question},
                )
                return self._build_repair_gate_ask_user_response(
                    active_deps=active_deps,
                    ask_user=ask_user,
                    session_id=session_id,
                    serialized_history=serialized_history,
                    run_messages=run_messages,
                    request=request,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business=business,
                )

            if review.force_ask_user and review.ask_user is not None:
                ask_user = review.ask_user
                if self._is_repair_followup_answer_request(request):
                    followup_query_state = followup_query_state or self._resolve_repair_followup_query_state(
                        request=request,
                        active_deps=active_deps,
                        message_history=full_messages,
                    )
                    original_query = followup_query_state.original_query
                    context = dict(ask_user.context or {})
                    if original_query:
                        context["query"] = original_query
                        context["repair_knowledge_query"] = original_query
                    ask_user = ask_user.model_copy(update={"context": context})
                active_deps.tracer.trace(
                    event_type="repair_answer_gate_review_ask_user",
                    session_id=session_id,
                    payload={"missing_field_keys": review.missing_field_keys},
                )
                return self._build_repair_gate_ask_user_response(
                    active_deps=active_deps,
                    ask_user=ask_user,
                    session_id=session_id,
                    serialized_history=serialized_history,
                    run_messages=run_messages,
                    request=request,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business="GENERAL_CHAT",
                )

            output = str(result.output or "").strip()
            if output != "__READY_TO_ANSWER__":
                active_deps.tracer.trace(
                    event_type="repair_answer_gate_unexpected_output",
                    session_id=session_id,
                    detail=output[:120],
                )
                if not review.allow_ready:
                    ask_user = review.ask_user
                    if ask_user is None:
                        loaded_context_for_followup = loaded_context if isinstance(loaded_context, dict) and loaded_context.get("loaded") else None
                        ask_user = await RepairKnowledgeFollowupAdapter.build_ask_user_question_async(
                            query=(
                                followup_query_state.original_query
                                if followup_query_state is not None
                                else review_query
                            ),
                            loaded_context=loaded_context_for_followup or {"loaded": False},
                            answer_text=str(output or ""),
                        )
                    if self._is_repair_followup_answer_request(request):
                        followup_query_state = followup_query_state or self._resolve_repair_followup_query_state(
                            request=request,
                            active_deps=active_deps,
                            message_history=full_messages,
                        )
                        original_query = followup_query_state.original_query
                        context = dict(ask_user.context or {})
                        if original_query:
                            context["query"] = original_query
                            context["repair_knowledge_query"] = original_query
                        ask_user = ask_user.model_copy(update={"context": context})
                    return self._build_repair_gate_ask_user_response(
                        active_deps=active_deps,
                        ask_user=ask_user,
                        session_id=session_id,
                        serialized_history=serialized_history,
                        run_messages=run_messages,
                        request=request,
                        request_id=request_id,
                        runtime_version=runtime_version,
                        business="GENERAL_CHAT",
                    )
                active_deps.tracer.trace(
                    event_type="repair_answer_gate_forced_ready_from_review",
                    session_id=session_id,
                    payload={"missing_field_keys": review.missing_field_keys},
                )

            if not review.allow_ready:
                active_deps.tracer.trace(
                    event_type="repair_answer_gate_review_blocked_ready",
                    session_id=session_id,
                    payload={"missing_field_keys": review.missing_field_keys},
                )
                ask_user = review.ask_user
                if ask_user is None:
                    loaded_context_for_followup = loaded_context if isinstance(loaded_context, dict) and loaded_context.get("loaded") else None
                    ask_user = await RepairKnowledgeFollowupAdapter.build_ask_user_question_async(
                        query=(
                            followup_query_state.original_query
                            if followup_query_state is not None
                            else review_query
                        ),
                        loaded_context=loaded_context_for_followup or {"loaded": False},
                        answer_text="",
                    )
                if self._is_repair_followup_answer_request(request):
                    followup_query_state = followup_query_state or self._resolve_repair_followup_query_state(
                        request=request,
                        active_deps=active_deps,
                        message_history=full_messages,
                    )
                    original_query = followup_query_state.original_query
                    context = dict(ask_user.context or {})
                    if original_query:
                        context["query"] = original_query
                        context["repair_knowledge_query"] = original_query
                    ask_user = ask_user.model_copy(update={"context": context})
                return self._build_repair_gate_ask_user_response(
                    active_deps=active_deps,
                    ask_user=ask_user,
                    session_id=session_id,
                    serialized_history=serialized_history,
                    run_messages=run_messages,
                    request=request,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business="GENERAL_CHAT",
                )

            self._persist_case_context_after_agent_run(
                active_deps=active_deps,
                run_messages=run_messages,
                request=request,
                ask_user=None,
                business="GENERAL_CHAT",
            )
            active_deps.tracer.trace(
                event_type="repair_answer_gate_ready",
                session_id=session_id,
                payload={"message_count": len(full_messages)},
            )
            return RepairAnswerGateReadyState(
                message_history=self._trim_repair_gate_ready_history(full_messages),
                query=(
                    followup_query_state.original_query
                    if followup_query_state is not None
                    else (
                        self._extract_latest_user_prompt(full_messages)
                        or str(request.message or "").strip()
                    )
                    or review_query
                ),
                run_messages=run_messages,
            )
        except Exception as exc:
            active_deps.tracer.trace(
                event_type="repair_answer_gate_failed",
                session_id=session_id,
                detail=str(exc),
            )
            return None

    def _build_repair_gate_ask_user_response(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        ask_user: AskUserQuestion,
        session_id: str,
        serialized_history: str,
        run_messages: Sequence[Any],
        request: ChatRequest,
        request_id: str,
        runtime_version: str | None,
        business: str,
    ) -> ChatResponse:
        full_messages = self._deserialize_history(serialized_history)
        synthetic_history = self._build_synthetic_ask_user_history(
            full_messages=full_messages,
            ask_user=ask_user,
        )
        synthetic_serialized_history = self._serialize_history(synthetic_history)
        active_deps.message_history_store.save_serialized_history(session_id, synthetic_serialized_history)
        active_deps.deferred_state_store.save(
            session_id=session_id,
            state=DeferredState(
                tool_call_id=ask_user.tool_call_id,
                tool_name="ask_user_question",
                message_history_json=synthetic_serialized_history,
                payload=self._build_ask_user_deferred_payload(ask_user=ask_user, business=business),
            ),
        )
        self._persist_case_context_after_agent_run(
            active_deps=active_deps,
            run_messages=run_messages,
            request=request,
            ask_user=ask_user,
            business=business,
        )
        return self._build_ask_user_response(
            ask_user=ask_user,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business=business,
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    def _normalize_runtime_ask_user_question(
        self,
        *,
        ask_user: AskUserQuestion,
        request: ChatRequest,
        full_messages: Sequence[Any] | None,
    ) -> AskUserQuestion:
        ask_user = normalize_ask_user_question_v2(ask_user)
        if not RepairKnowledgeFollowupAdapter.is_repair_followup_context(ask_user.context):
            return ask_user

        loaded_context = self._extract_loaded_repair_knowledge_context(full_messages or [])
        query = (
            str((ask_user.context or {}).get("repair_knowledge_query") or "").strip()
            or str((ask_user.context or {}).get("query") or "").strip()
            or self._extract_latest_user_prompt(full_messages or [])
            or str(request.message or "").strip()
        )
        return RepairKnowledgeFollowupAdapter.normalize_ask_user_question(
            ask_user,
            query=query,
            loaded_context=loaded_context,
        )

    async def _normalize_runtime_ask_user_question_async(
        self,
        *,
        ask_user: AskUserQuestion,
        request: ChatRequest,
        full_messages: Sequence[Any] | None,
    ) -> AskUserQuestion:
        ask_user = await normalize_ask_user_question_v2_async(ask_user)
        if not RepairKnowledgeFollowupAdapter.is_repair_followup_context(ask_user.context):
            return ask_user

        loaded_context = self._extract_loaded_repair_knowledge_context(full_messages or [])
        query = (
            str((ask_user.context or {}).get("repair_knowledge_query") or "").strip()
            or str((ask_user.context or {}).get("query") or "").strip()
            or self._extract_latest_user_prompt(full_messages or [])
            or str(request.message or "").strip()
        )
        return await RepairKnowledgeFollowupAdapter.normalize_ask_user_question_async(
            ask_user,
            query=query,
            loaded_context=loaded_context,
        )

    @staticmethod
    def _trim_repair_gate_ready_history(messages: Sequence[Any]) -> Sequence[Any]:
        from pydantic_ai.messages import ModelResponse, TextPart

        trimmed = list(messages)
        if not trimmed:
            return trimmed

        last_message = trimmed[-1]
        if not isinstance(last_message, ModelResponse):
            return trimmed

        text_parts = [part.content for part in last_message.parts if isinstance(part, TextPart)]
        if text_parts and "".join(text_parts).strip() == "__READY_TO_ANSWER__":
            return trimmed[:-1]
        return trimmed

    def _build_repair_renderer_prompt(self, request: ChatRequest) -> str:
        if request.ask_user_answer is not None:
            return (
                "请基于已加载的维修资料、共享上下文以及用户刚补充的信息，"
                "直接给出当前最稳妥的最终答复。"
                "优先按“故障定义 -> 当前更像哪一型 -> 可能原因分类 -> 分步检查 -> 判断依据 -> 维修处理 -> 易误判点”组织。"
                "第一节正文第一句必须以“老哥，”开头。"
                "禁止再次调用 ask_user_question。"
            )

        query = (request.message or "").strip()
        return (
            "请基于已加载的维修资料和共享上下文，"
            f"直接回答用户当前问题：{query}。"
            "优先按“故障定义 -> 当前更像哪一型 -> 可能原因分类 -> 分步检查 -> 判断依据 -> 维修处理 -> 易误判点”组织。"
            "第一节正文第一句必须以“老哥，”开头。"
            "禁止再次调用 ask_user_question。"
        )

    async def _prepare_repair_render_runtime_state(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        ready_state: RepairAnswerGateReadyState,
        repair_render_planner_agent: Any | None,
    ) -> RepairRenderRuntimeState:
        followup_state = self._resolve_repair_followup_summary(
            request=request,
            active_deps=active_deps,
            message_history=ready_state.message_history,
        )
        loaded_context = self._extract_loaded_repair_knowledge_context(ready_state.message_history) or {}
        render_context = build_repair_render_context(
            query=ready_state.query,
            summary_text=followup_state.summary_text,
            flattened_fields=self._flatten_repair_followup_fields(followup_state.field_values),
            loaded_context=loaded_context,
        )
        render_plan = await self._run_repair_render_planner(
            active_deps=active_deps,
            ready_state=ready_state,
            render_context=render_context,
            repair_render_planner_agent=repair_render_planner_agent,
        )
        render_prompt = self._build_repair_renderer_prompt_v2(
            plan=render_plan,
            context=render_context,
        )
        return RepairRenderRuntimeState(
            message_history=ready_state.message_history,
            user_prompt=render_prompt,
            run_messages=ready_state.run_messages,
            plan=render_plan,
            context=render_context,
        )

    async def _run_repair_render_planner(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        ready_state: RepairAnswerGateReadyState,
        render_context: RepairRenderContext,
        repair_render_planner_agent: Any | None,
    ) -> RepairRenderPlan:
        fallback_plan = default_repair_render_plan(render_context)
        if repair_render_planner_agent is None:
            return fallback_plan

        planner_prompt = self._build_repair_render_planner_prompt(render_context)
        try:
            llm_started_at = time.perf_counter()
            result = await repair_render_planner_agent.run(
                user_prompt=planner_prompt,
                deps=active_deps,
                message_history=ready_state.message_history,
            )
            self._record_llm_run_observability(
                active_deps=active_deps,
                session_id=getattr(active_deps, "request_session_id", None) or "",
                result=result,
                llm_started_at=llm_started_at,
                phase="repair_render_planner",
            )
            candidate = result.output
            if isinstance(candidate, dict):
                candidate = RepairRenderPlan.model_validate(candidate)
            if not isinstance(candidate, RepairRenderPlan):
                return fallback_plan
            valid, reasons = validate_repair_render_plan(candidate, context=render_context)
            if not valid:
                active_deps.tracer.trace(
                    event_type="repair_render_plan_invalid",
                    session_id=getattr(active_deps, "request_session_id", None),
                    payload={"reasons": reasons, "candidate": candidate.model_dump(mode="json")},
                )
                return fallback_plan
            active_deps.tracer.trace(
                event_type="repair_render_plan_selected",
                session_id=getattr(active_deps, "request_session_id", None),
                payload=candidate.model_dump(mode="json"),
            )
            return candidate
        except Exception as exc:
            active_deps.tracer.trace(
                event_type="repair_render_plan_failed",
                session_id=getattr(active_deps, "request_session_id", None),
                detail=str(exc),
            )
            return fallback_plan

    @staticmethod
    def _build_repair_render_planner_prompt(context: RepairRenderContext) -> str:
        parts = [
            "请只输出结构化 RepairRenderPlan。",
            f"当前问题：{context.query}",
        ]
        if context.summary_text:
            parts.append(f"用户补充摘要：{context.summary_text}")
        if context.flattened_fields:
            parts.append(f"结构化补充：{context.flattened_fields}")
        if context.source_titles:
            parts.append(f"已加载资料：{'；'.join(context.source_titles)}")
        parts.append(
            "判断原则：不要默认用症状诊断模板；参数值走 spec_answer，原理走 principle_explanation，位置/区分走 location_identification，使用步骤走 operation_guide，报码走 dtc_diagnosis，其余排查类走 symptom_diagnosis。"
        )
        parts.append(
            "你还必须同时决定 answer_depth、required_elements、optional_elements、min_steps、need_thresholds、need_branching、need_recheck。"
            "能直接回答的用 direct；需要正常步骤的用 standard；需要师傅现场按分支排的用 playbook。"
        )
        return "\n".join(parts)

    @staticmethod
    def _build_repair_renderer_prompt_v2(
        *,
        plan: RepairRenderPlan,
        context: RepairRenderContext,
    ) -> str:
        strategy = get_repair_render_strategy(plan.frame)
        return strategy.build_prompt(plan=plan, context=context)

    async def _maybe_retry_repair_render_output(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        repair_renderer_agent: Any | None,
        render_state: RepairRenderRuntimeState | None,
        output: Any,
        serialized_history: str,
    ) -> tuple[Any, str]:
        if repair_renderer_agent is None or render_state is None or not isinstance(output, str):
            return output, serialized_history

        review = review_repair_rendered_answer(
            content=output,
            plan=render_state.plan,
            context=render_state.context,
        )
        if review.accepted:
            return output, serialized_history

        strategy = get_repair_render_strategy(render_state.plan.frame)
        retry_prompt = strategy.build_retry_prompt(
            plan=render_state.plan,
            context=render_state.context,
            previous_answer=review.content or output,
            reasons=review.reasons,
        )
        active_deps.tracer.trace(
            event_type="repair_render_retry_requested",
            session_id=getattr(active_deps, "request_session_id", None),
            payload={"reasons": review.reasons, "frame": render_state.plan.frame.value},
        )
        try:
            llm_started_at = time.perf_counter()
            retry_result = await repair_renderer_agent.run(
                user_prompt=retry_prompt,
                deps=active_deps,
                message_history=render_state.message_history,
                deferred_tool_results=None,
            )
            self._record_llm_run_observability(
                active_deps=active_deps,
                session_id=getattr(active_deps, "request_session_id", None) or "",
                result=retry_result,
                llm_started_at=llm_started_at,
                phase="repair_render_retry",
            )
            active_deps.tracer.trace(
                event_type="repair_render_retry_completed",
                session_id=getattr(active_deps, "request_session_id", None),
                payload={"output_type": type(retry_result.output).__name__},
            )
            return retry_result.output, retry_result.all_messages_json().decode("utf-8")
        except Exception as exc:
            active_deps.tracer.trace(
                event_type="repair_render_retry_failed",
                session_id=getattr(active_deps, "request_session_id", None),
                detail=str(exc),
            )
            return output, serialized_history

    def _finalize_repair_rendered_content(
        self,
        *,
        content: Any,
        extra_metadata: dict[str, Any] | None,
        render_state: RepairRenderRuntimeState,
    ) -> tuple[Any, dict[str, Any] | None]:
        if not isinstance(content, str):
            metadata = dict(extra_metadata or {})
            metadata["repair_render_plan"] = render_state.plan.model_dump(mode="json")
            metadata["repair_render_frame"] = render_state.plan.frame.value
            return content, metadata

        metadata = dict(extra_metadata or {})
        metadata["repair_render_plan"] = render_state.plan.model_dump(mode="json")
        metadata["repair_render_frame"] = render_state.plan.frame.value

        review = review_repair_rendered_answer(
            content=content,
            plan=render_state.plan,
            context=render_state.context,
        )
        if review.accepted:
            return review.content, metadata

        metadata["repair_render_review_failed"] = True
        metadata["repair_render_review_reasons"] = review.reasons
        return RepairKnowledgeFollowupAdapter.normalize_user_facing_message(content), metadata

    @staticmethod
    def _build_repair_guideline_content(
        *,
        fault_definition: str,
        diagnosis_type: str,
        cause_groups: list[str],
        check_steps: list[str],
        judgment_points: list[str],
        repair_actions: list[str],
        cautions: list[str],
    ) -> str:
        normalized_fault_definition = fault_definition.strip()
        if normalized_fault_definition and not normalized_fault_definition.startswith("老哥，"):
            normalized_fault_definition = f"老哥，{normalized_fault_definition}"

        def _join_lines(lines: list[str]) -> str:
            return "\n".join(line.strip() for line in lines if str(line).strip())

        sections = [
            f"### 故障定义\n{normalized_fault_definition}",
            f"### 当前更像哪一型\n{diagnosis_type.strip()}",
            f"### 可能原因分类\n{_join_lines(cause_groups)}",
            f"### 分步检查\n{_join_lines(check_steps)}",
            f"### 判断依据\n{_join_lines(judgment_points)}",
            f"### 维修处理\n{_join_lines(repair_actions)}",
            f"### 易误判点\n{_join_lines(cautions)}",
        ]
        return "\n\n".join(section for section in sections if section.strip())

    def _build_repair_resume_fallback_response(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        message_history: Sequence[Any] | None,
        user_prompt: str | None,
        exc: Exception,
    ) -> ChatResponse | None:
        if request.ask_user_answer is None or not message_history:
            return None

        loaded_context = self._extract_loaded_repair_knowledge_context(message_history) or {}
        query = (
            self._resolve_repair_followup_query_state(
                request=request,
                active_deps=active_deps,
                message_history=message_history,
            ).original_query
            if self._is_repair_followup_answer_request(request)
            else (self._extract_latest_user_prompt(message_history) or (request.message or "").strip())
        )
        followup_state = self._resolve_repair_followup_summary(
            request=request,
            active_deps=active_deps,
            message_history=message_history,
        )
        content = self._build_repair_renderer_fallback_content(
            query=query,
            summary_text=followup_state.summary_text,
            field_values=followup_state.field_values,
            loaded_context=loaded_context,
        )
        if not content.strip():
            return None

        self._persist_synthetic_message_history(
            active_deps=active_deps,
            session_id=session_id,
            base_messages=message_history,
            user_prompt=user_prompt,
            content=content,
        )

        metadata = {
            "repair_renderer_fallback": True,
        }
        repair_knowledge_metadata = self._extract_repair_knowledge_metadata(message_history)
        if repair_knowledge_metadata:
            metadata.update(repair_knowledge_metadata)
        if self._is_repair_followup_answer_request(request):
            metadata["repair_followup_rewritten"] = True
        active_deps.tracer.trace(
            event_type="repair_renderer_fallback",
            session_id=session_id,
            detail=str(exc),
            payload={
                "query": query,
                "summary_text": followup_state.summary_text,
                "primary_source": (loaded_context.get("primary_source") or {}).get("title"),
            },
        )
        return self._build_message_response(
            content=content,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business="GENERAL_CHAT",
            extra_metadata=metadata,
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    def _build_repair_renderer_fallback_response(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        ready_state: RepairAnswerGateReadyState,
        render_state: RepairRenderRuntimeState | None,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        exc: Exception,
    ) -> ChatResponse | None:
        if render_state is not None:
            content = build_repair_render_fallback_content(
                plan=render_state.plan,
                context=render_state.context,
            )
            metadata = {
                "repair_renderer_fallback": True,
                "repair_render_plan": render_state.plan.model_dump(mode="json"),
                "repair_render_frame": render_state.plan.frame.value,
            }
            if self._is_repair_followup_answer_request(request):
                metadata["repair_followup_rewritten"] = True
            return self._build_message_response(
                content=content,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="GENERAL_CHAT",
                extra_metadata=metadata,
                llm_observability=getattr(active_deps, "llm_observability", None),
            )
        return self._build_repair_resume_fallback_response(
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            message_history=ready_state.message_history,
            user_prompt=ready_state.query,
            exc=exc,
        )

    @staticmethod
    def _extract_repair_followup_summary(
        answer: AskUserAnswer | None,
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        payload = answer.answer if answer is not None else None
        return AgentLoopService._extract_repair_followup_summary_from_payload(payload)

    def _resolve_repair_followup_summary(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        message_history: Sequence[Any] | None,
    ) -> RepairFollowupSummaryState:
        payloads = self._collect_repair_followup_payloads_from_message_history(message_history)
        payloads.extend(self._collect_repair_followup_payloads_from_case_context(active_deps.case_context))
        if request.ask_user_answer is not None:
            payloads.append(request.ask_user_answer.answer)
        payloads = self._dedupe_repair_followup_payloads(payloads)
        if not payloads:
            summary_text, field_values = self._extract_repair_followup_summary(request.ask_user_answer)
            return RepairFollowupSummaryState(summary_text=summary_text, field_values=field_values)
        summary_text, field_values = self._merge_repair_followup_payloads(payloads)
        return RepairFollowupSummaryState(summary_text=summary_text, field_values=field_values)

    def _resolve_repair_followup_query_state(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        message_history: Sequence[Any] | None,
    ) -> RepairFollowupQueryState:
        if not self._is_repair_followup_answer_request(request):
            original_query = (request.message or "").strip()
            return RepairFollowupQueryState(
                original_query=original_query,
                evidence_query=original_query,
            )

        candidates = [
            self._extract_repair_followup_query_from_deferred_state(
                session_id=getattr(active_deps, "request_session_id", None),
                active_deps=active_deps,
                answer=request.ask_user_answer,
            ),
            self._extract_repair_followup_query_from_message_history(message_history),
            self._extract_repair_followup_query_from_case_context(active_deps.case_context),
            self._clean_repair_followup_query_candidate(request.message),
        ]
        original_query = ""
        for candidate in candidates:
            if candidate:
                original_query = candidate
                break
        if not original_query:
            current_message = self._clean_repair_followup_query_candidate(request.message)
            if current_message:
                original_query = current_message

        followup_state = self._resolve_repair_followup_summary(
            request=request,
            active_deps=active_deps,
            message_history=message_history,
        )
        evidence_query = "\n".join(
            part
            for part in [
                original_query,
                followup_state.summary_text,
                self._flatten_repair_followup_fields(followup_state.field_values),
            ]
            if part
        ).strip()
        return RepairFollowupQueryState(
            original_query=original_query,
            evidence_query=evidence_query or original_query,
        )

    def _extract_repair_followup_query_from_deferred_state(
        self,
        *,
        session_id: str | None,
        active_deps: AgentRuntimeDeps,
        answer: AskUserAnswer | None,
    ) -> str | None:
        if not session_id or answer is None:
            return None
        deferred_state = active_deps.deferred_state_store.load(
            session_id=session_id,
            tool_call_id=answer.tool_call_id,
        )
        if deferred_state is None:
            return None
        return self._extract_repair_followup_query_from_payload(deferred_state.payload)

    @staticmethod
    def _extract_repair_followup_query_from_payload(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None

        candidates: list[Any] = []
        ask_user_payload = payload.get("ask_user")
        if isinstance(ask_user_payload, dict):
            candidates.append(ask_user_payload)
        candidates.append(payload)

        for candidate in candidates:
            context = candidate.get("context") if isinstance(candidate, dict) else None
            if not isinstance(context, dict):
                continue
            query = AgentLoopService._clean_repair_followup_query_candidate(
                context.get("repair_knowledge_query") or context.get("query")
            )
            if query:
                return query
        return None

    @staticmethod
    def _extract_repair_followup_query_from_message_history(messages: Sequence[Any] | None) -> str | None:
        if not messages:
            return None

        from pydantic_ai.messages import ModelResponse, ToolCallPart

        for message in reversed(messages):
            if not isinstance(message, ModelResponse):
                continue
            for part in reversed(message.parts):
                if not isinstance(part, ToolCallPart) or part.tool_name != "ask_user_question":
                    continue
                args = part.args if isinstance(part.args, dict) else {}
                context = args.get("context") if isinstance(args.get("context"), dict) else {}
                query = AgentLoopService._clean_repair_followup_query_candidate(
                    context.get("repair_knowledge_query") or context.get("query")
                )
                if query:
                    return query
        return None

    @staticmethod
    def _extract_repair_followup_query_from_case_context(case_context: Any | None) -> str | None:
        if case_context is None:
            return None

        from app.agent.context.models import CaseContextArtifactType

        for artifact in reversed(getattr(case_context, "artifacts", [])):
            if getattr(artifact, "type", None) != CaseContextArtifactType.PENDING_ACTION:
                continue
            structured_data = artifact.structured_data if isinstance(artifact.structured_data, dict) else {}
            context = structured_data.get("context") if isinstance(structured_data.get("context"), dict) else {}
            query = AgentLoopService._clean_repair_followup_query_candidate(
                context.get("repair_knowledge_query") or context.get("query")
            )
            if query:
                return query
        return None

    @staticmethod
    def _clean_repair_followup_query_candidate(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        disallowed_fragments = (
            "[REPAIR_FOLLOWUP_RESUME]",
            "[CASE_CONTEXT]",
            "[CURRENT_USER_MESSAGE]",
            "请基于已加载资料、共享上下文和用户刚补充的信息继续判断当前是否还需要追问。",
        )
        if any(fragment in text for fragment in disallowed_fragments):
            return None
        return text

    @staticmethod
    def _dedupe_repair_followup_payloads(payloads: Sequence[Any]) -> list[Any]:
        deduped: list[Any] = []
        seen: set[str] = set()
        for payload in payloads:
            signature = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(payload)
        return deduped

    @staticmethod
    def _collect_repair_followup_payloads_from_message_history(messages: Sequence[Any] | None) -> list[Any]:
        if not messages:
            return []

        from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

        payloads: list[Any] = []
        seen_followup_answer = False
        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue

            for part in reversed(message.parts):
                if not isinstance(part, ToolReturnPart) or part.tool_name != "ask_user_question":
                    continue
                content = part.content if isinstance(part.content, dict) else {}
                payload = content.get("answer") if isinstance(content, dict) else None
                if AgentLoopService._is_repair_followup_payload(payload):
                    payloads.append(payload)
                    seen_followup_answer = True

            if seen_followup_answer and any(
                isinstance(part, UserPromptPart) and isinstance(part.content, str) and part.content.strip()
                for part in message.parts
            ):
                break

        payloads.reverse()
        return payloads

    @staticmethod
    def _collect_repair_followup_payloads_from_case_context(case_context: Any | None) -> list[Any]:
        if case_context is None:
            return []

        from app.agent.context.models import CaseContextArtifactType

        payloads: list[Any] = []
        for artifact in reversed(getattr(case_context, "artifacts", [])):
            if getattr(artifact, "type", None) != CaseContextArtifactType.USER_ANSWER:
                continue
            structured_data = artifact.structured_data if isinstance(artifact.structured_data, dict) else {}
            payload = structured_data.get("answer")
            if AgentLoopService._is_repair_followup_payload(payload):
                payloads.append(payload)
                continue
            if payloads:
                break

        payloads.reverse()
        return payloads

    @staticmethod
    def _merge_repair_followup_payloads(payloads: Sequence[Any]) -> tuple[str, dict[str, dict[str, Any]]]:
        merged_fields: dict[str, dict[str, Any]] = {}
        fallback_summaries: list[str] = []

        for payload in payloads:
            summary_text, field_values = AgentLoopService._extract_repair_followup_summary_from_payload(payload)
            if summary_text:
                fallback_summaries.append(summary_text)
            for key, item in field_values.items():
                merged_fields[key] = {
                    "selected": [str(value).strip() for value in (item.get("selected") or []) if str(value).strip()],
                    "text": str(item.get("text") or "").strip(),
                }

        summary_text = AgentLoopService._build_repair_followup_summary_text(merged_fields)
        if summary_text:
            return summary_text, merged_fields

        for summary_text in reversed(fallback_summaries):
            if summary_text:
                return summary_text, merged_fields
        return "", merged_fields

    @staticmethod
    def _extract_repair_followup_summary_from_payload(
        payload: Any,
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        if not isinstance(payload, dict):
            text = str(payload or "").strip()
            return text, {}

        raw_fields = payload.get("fields") or {}
        field_values: dict[str, dict[str, Any]] = {}
        for key, raw_value in raw_fields.items():
            selected: list[str] = []
            text_value = ""
            if isinstance(raw_value, dict):
                selected = [str(item).strip() for item in (raw_value.get("selected") or []) if str(item).strip()]
                text_value = str(raw_value.get("text") or "").strip()
            else:
                text_value = str(raw_value or "").strip()
            if str(key) == "fault_codes":
                status_only = {"有明确故障码", "故障灯亮但未读取具体报码", "报码偶发", "无报码", "暂无故障码"}
                selected = [item for item in selected if item not in status_only]
            field_values[str(key)] = {"selected": selected, "text": text_value}

        summary_text = str(payload.get("summary_text") or "").strip()
        if summary_text:
            return summary_text, field_values

        return AgentLoopService._build_repair_followup_summary_text(field_values), field_values

    @staticmethod
    def _build_repair_followup_summary_text(
        field_values: dict[str, dict[str, Any]],
    ) -> str:
        label_map = {
            "ecu_or_system": "车辆/系统",
            "fault_phenomenon": "故障现象",
            "working_condition": "出现条件",
            "fault_codes": "故障码情况",
            "data_evidence": "已知数据",
            "repair_history": "维修历史",
        }
        parts: list[str] = []
        known_keys = (
            "ecu_or_system",
            "fault_phenomenon",
            "working_condition",
            "fault_codes",
            "data_evidence",
            "repair_history",
        )
        for key in known_keys:
            item = field_values.get(key) or {}
            values = [str(entry).strip() for entry in (item.get("selected") or []) if str(entry).strip()]
            text_value = str(item.get("text") or "").strip()
            if text_value:
                values.append(text_value)
            if not values:
                continue
            parts.append(f"{label_map.get(key, key)}：{'、'.join(values)}")

        for key, item in field_values.items():
            if key in known_keys:
                continue
            values = [str(entry).strip() for entry in (item.get("selected") or []) if str(entry).strip()]
            text_value = str(item.get("text") or "").strip()
            if text_value:
                values.append(text_value)
            if not values:
                continue
            parts.append(f"{label_map.get(key, key)}：{'、'.join(values)}")
        return "；".join(parts)

    def _build_repair_renderer_fallback_content(
        self,
        *,
        query: str,
        summary_text: str,
        field_values: dict[str, dict[str, Any]],
        loaded_context: dict[str, Any],
    ) -> str:
        render_context = build_repair_render_context(
            query=query,
            summary_text=summary_text,
            flattened_fields=self._flatten_repair_followup_fields(field_values),
            loaded_context=loaded_context,
        )
        render_plan = default_repair_render_plan(render_context)
        return build_repair_render_fallback_content(
            plan=render_plan,
            context=render_context,
        )

    @staticmethod
    def _flatten_repair_followup_fields(field_values: dict[str, dict[str, Any]]) -> str:
        fragments: list[str] = []
        for key, item in field_values.items():
            selected = [str(value).strip() for value in (item.get("selected") or []) if str(value).strip()]
            text_value = str(item.get("text") or "").strip()
            if text_value:
                selected.append(text_value)
            if selected:
                fragments.append(f"{key}:{' '.join(selected)}")
        return "\n".join(fragments)

    def _build_start_issue_fallback_content(
        self,
        *,
        summary_text: str,
        field_values: dict[str, dict[str, Any]],
        start_profile: str,
    ) -> str:
        phenomenon = self._repair_field_text(field_values, "fault_phenomenon")
        working = self._repair_field_text(field_values, "working_condition")
        fault_codes = self._repair_field_text(field_values, "fault_codes")
        repair_history = self._repair_field_text(field_values, "repair_history")
        known = summary_text or "当前已知是启动相关故障。"

        is_cranks_no_start = any(
            hint in phenomenon for hint in ("能转但发动机不着车", "能转但不着车", "正常但不着车")
        )
        is_no_crank = any(
            hint in phenomenon for hint in ("无反应", "咔哒", "不转", "吸合但不转", "起动机无反应")
        )
        has_security_code = any(hint in fault_codes for hint in ("防盗", "启动许可"))
        has_voltage_code = "供电电压" in fault_codes
        has_sync_code = any(hint in fault_codes for hint in ("曲轴", "凸轮", "同步"))

        if is_cranks_no_start:
            check_lines = [
                "1. 先看启动许可是否真正放行。优先读取钥匙识别、启动许可、发动机允许起动这几个状态，不要先拆起动机。",
                "2. 再看起动时发动机转速和曲轴/凸轮轴同步状态，确认 ECU 有没有拿到允许喷油的基本条件。",
                "3. 许可和同步都正常后，再看启动瞬间轨压或供油建立速度；冷车明显时，把低压侧进气、回油过大、计量阀卡滞放到前面。",
            ]
            if has_security_code:
                check_lines[0] = "1. 已经有防盗/启动许可相关报码时，先把启动许可放在第一位。直接看钥匙识别、启动许可、发动机允许起动状态；只要许可没放行，就先查防盗天线、钥匙匹配、点火锁和车身控制链路。"
            if has_sync_code:
                check_lines[1] = "2. 报码已经带到曲轴/凸轮轴信号方向时，起动时重点盯发动机转速和同步状态；如果无转速或不同步，优先查曲轴/凸轮轴传感器、插头、间隙和线束。"
            if repair_history:
                check_lines.append("4. 如果近期做过搭电、换电瓶、换锁头或动过线束，再补查搭铁点、保险、电源分配和相关插头接触。")
            return self._build_repair_guideline_content(
                fault_definition=(
                    f"结合你补充的情况：{known}。这不是“起动机不工作”，而是“起动机能带动发动机转，但发动机没有正常着车”。"
                    "当前诊断任务是先分清：到底是启动许可没放行、转速/同步条件不成立，还是燃油建压在冷车阶段起不来。"
                ),
                diagnosis_type="更像“启动许可/同步信号/建压不上”这一型，主线要先放在允许起动条件和建压条件，不先把问题落到起动机本体。",
                cause_groups=[
                    "1. 启动许可链路：防盗识别失败、钥匙匹配异常、点火锁或 BCM 未下发允许起动信号。",
                    "2. 同步与转速链路：曲轴/凸轮轴传感器、线束、间隙异常，导致 ECU 虽然看到起动机在转，但不允许喷油。",
                    "3. 燃油建压链路：低压侧进气、喷油器回油偏大、计量阀卡滞或高压侧泄漏，尤其冷车更明显。",
                ],
                check_steps=check_lines,
                judgment_points=[
                    "1. 如果启动许可状态异常，先沿授权链路查，当前不成立的是“允许着车”条件，不是起动机本体故障。",
                    "2. 如果许可正常但无转速、转速异常或不同步，优先查曲轴/凸轮轴信号，不要直接怀疑油路。",
                    "3. 如果许可和同步都正常，但起动时目标条件已满足而轨压长时间起不来，再把重点转到燃油系统建压。",
                    "4. 冷车明显、排气或处理低压油路后短时好转时，优先怀疑低压侧进气、回空或冷态泄漏。",
                ],
                repair_actions=[
                    "1. 启动许可异常：修防盗识别、钥匙匹配、点火锁输入或 BCM 到 ECU 的许可链路。",
                    "2. 同步异常：修复曲轴/凸轮轴传感器、插头、线束或安装间隙问题。",
                    "3. 建压异常：排查低压油路进气、喷油器回油、计量阀和高压侧泄漏，再决定是否进一步落到高压泵本体。",
                ],
                cautions=[
                    "1. 起动机能带动发动机转，不等于发动机具备正常着车条件。",
                    "2. 没先确认许可和同步前，不要先拆起动机，也不要直接判高压泵。",
                    "3. 冷车明显时，低压侧进气和冷态泄漏的优先级通常比“总成直接损坏”更高。",
                ],
            )

        if is_no_crank or start_profile == "starter_motor":
            check_lines = [
                "1. 先量电瓶静态电压和起动瞬间压降，同时摸主火线、搭铁线和接线柱是否发热或松动；供电一掉下去，后面的判断都会失真。",
                "2. 再看点火开关、起动继电器、50 端起动信号是否真的送到位；打钥匙只有咔哒或偶发无反应时，这一步优先级很高。",
                "3. 电源、搭铁和控制都正常，再查起动机本体是否卡滞、吸合开关是否烧蚀，必要时再做台架确认。",
            ]
            if has_voltage_code:
                check_lines[0] = "1. 已经带出供电电压相关报码时，先量电瓶静态电压和起动瞬间压降，再查正极主火线、搭铁带、保险盒大电流回路和接线柱压降。"
            return self._build_repair_guideline_content(
                fault_definition=(
                    f"结合你补充的情况：{known}。这类属于起动回路故障，当前任务不是泛泛判断“启动不了”，而是把故障拆成供电、控制、执行三段去定位。"
                ),
                diagnosis_type="更像“起动回路不成立”这一型，主线按“电源和搭铁 -> 起动控制 -> 起动机本体”排，不反过来查。",
                cause_groups=[
                    "1. 供电与搭铁：电瓶电量不足、正极主火线压降大、搭铁带接触不良、大电流接点发热。",
                    "2. 起动控制：点火开关、起动继电器、50 端控制信号、BCM 或启动许可链路异常。",
                    "3. 执行机构：起动机吸合开关烧蚀、机械卡滞、线圈异常或内部接触不良。",
                ],
                check_steps=check_lines,
                judgment_points=[
                    "1. 如果起动瞬间压降异常，先处理供电和搭铁，否则后面的控制判断都会失真。",
                    "2. 如果 50 端没有控制信号，问题在起动控制链路，不在起动机本体。",
                    "3. 如果电源、搭铁、控制都到位但起动机仍不动作，再落到起动机总成。",
                ],
                repair_actions=[
                    "1. 修复电瓶状态、主火线、搭铁带、保险盒大电流回路和接线柱压降异常。",
                    "2. 修复点火开关、起动继电器或控制线束问题。",
                    "3. 控制条件全部成立后，检修或更换起动机总成及吸合开关。",
                ],
                cautions=[
                    "1. 只听到咔哒声，不等于一定是起动机坏，很多时候是压降或控制链路问题。",
                    "2. 没确认压降和 50 端信号前，不建议直接换起动机。",
                    "3. 带电跨接、强行搭电这类动作风险高，必须先确认线路状态和安全前提。",
                ],
            )

        temperature_note = ""
        if "冷车" in working or start_profile == "cold_start":
            temperature_note = "冷车明显时，优先看预热、轨压建立、温度信号和油路进空气。"
        elif "热车" in working or start_profile == "hot_start":
            temperature_note = "热车明显时，优先看传感器热衰减、供电掉压和热浸后的轨压建立。"

        judgment_points = [
            "1. 启动类问题先分清是许可条件不成立、同步条件不成立，还是建压条件不成立。",
            "2. 如果报码方向和数据流主线一致，就沿主线往下查；如果报码和现象明显对不上，要回头确认信号真伪和线束状态。",
        ]
        if temperature_note:
            judgment_points.append(f"3. {temperature_note}")
        else:
            judgment_points.append("3. 先把许可、转速同步、供油建立这三项查清，再决定是否往电路或总成深挖。")
        return self._build_repair_guideline_content(
            fault_definition=f"结合你补充的情况：{known}。当前属于启动类故障，需要先把故障转成“许可/同步/供油”三条主线来排，而不是泛泛地猜总成。",
            diagnosis_type="更像通用启动类故障，需要先分型，再决定往控制侧、信号侧还是供油侧深入。",
            cause_groups=[
                "1. 允许起动条件异常：防盗、点火许可、控制模块链路问题。",
                "2. 转速与同步条件异常：曲轴/凸轮轴信号、线束或传感器问题。",
                "3. 供油与建压条件异常：低压油路、喷油器回油、计量阀或高压侧泄漏。",
            ],
            check_steps=[
                "1. 先确认启动许可、报码方向和起动时发动机转速是否正常。",
                "2. 再看曲轴/凸轮轴同步、启动瞬间轨压或供油建立情况。",
                "3. 如果电源、电压或搭铁有异常迹象，再补查电瓶、搭铁带和起动回路。",
            ],
            judgment_points=judgment_points,
            repair_actions=[
                "1. 先把控制条件和信号条件修通，再处理供油或建压问题。",
                "2. 哪条主线先证实有问题，就优先修哪条，不要并行拆很多总成。",
            ],
            cautions=[
                "1. 启动类故障最怕一上来就把所有原因混在一起说，这样现场很容易无效拆装。",
                "2. 先易后难、先外后内，通常比直接换件更快定位。",
            ],
        )

    @staticmethod
    def _build_communication_fallback_content(*, summary_text: str) -> str:
        known = summary_text or "当前是 J1939/CAN 通讯相关故障。"
        return AgentLoopService._build_repair_guideline_content(
            fault_definition=f"结合当前已知情况：{known}。这类问题不是简单的“报码很多”，而是要先判断网络本体、节点供电还是单个模块拖垮总线。",
            diagnosis_type="更像 J1939/CAN 通讯故障，主线按“主干网络 -> 供电搭铁 -> 单节点拖垮”三段排。",
            cause_groups=[
                "1. 主干网络本体异常：终端电阻缺失、主干断路、支路短接、CAN_H/CAN_L 对地或相互短路。",
                "2. 公共供电与搭铁异常：多个模块共用的电源或搭铁掉电，导致整条网络报码或离线。",
                "3. 单节点拖垮：某个控制器、加装设备或受潮插头把网络电平拉坏。",
            ],
            check_steps=[
                "1. 先断电测主干电阻，正常通常接近 60 欧；明显偏高或偏低时，先修网络本体，不急着换模块。",
                "2. 再通电测 CAN_H/CAN_L 对地电压，同时看是单模块离线还是多个模块一起离线。",
                "3. 如果最近动过控制器、线束或加装设备，优先分段拔插隔离，找有没有单个节点把总线拖死。",
            ],
            judgment_points=[
                "1. 电阻不对，优先判断终端、电缆和支路短路，当前主问题在网络本体。",
                "2. 电阻正常但电压被明显拉偏，优先找短路节点或异常模块。",
                "3. 多个模块同时离线时，先查公共供电和搭铁；单模块离线时，再回到该模块本体、插头和分支线。",
            ],
            repair_actions=[
                "1. 修复终端电阻、主干断路、支路短路或受潮插头。",
                "2. 修复公共供电和搭铁异常。",
                "3. 隔离并修复拖垮总线的异常节点或加装设备。",
            ],
            cautions=[
                "1. 通讯故障最怕一报码就直接换模块，很多时候主问题在网络本体或公共供电。",
                "2. 没先量电阻和电压前，不要急着判控制器坏。",
            ],
        )

    @staticmethod
    def _build_power_loss_fallback_content(*, summary_text: str) -> str:
        known = summary_text or "当前是动力不足相关故障。"
        return AgentLoopService._build_repair_guideline_content(
            fault_definition=f"结合当前已知情况：{known}。这类故障不能只回答“原因很多”，而要先判断当前是供油跟不上、增压起不来，还是系统限扭先介入。",
            diagnosis_type="更像动力不足类故障，主线先按“轨压/供油 -> 进气增压 -> 限扭策略”分型。",
            cause_groups=[
                "1. 燃油供给侧：低压供油不足、轨压建立慢、高压侧泄漏或喷油器回油偏大。",
                "2. 进气增压侧：进气阻力大、增压控制异常、泄漏或执行器动作不对。",
                "3. 控制策略侧：报码触发限扭、保护策略介入，导致主观感觉是“无力”。",
            ],
            check_steps=[
                "1. 先把故障码和关键数据流对上，重点看目标/实际轨压、进气压力、增压压力和限扭状态。",
                "2. 如果急加速、爬坡或重载更明显，优先查燃油供给和增压系统，不先猜总成。",
                "3. 数据没有明显跑偏前，不要直接判高压泵、喷油器或增压器本体。",
            ],
            judgment_points=[
                "1. 目标轨压已经抬高但实际跟不上，优先沿油路和建压链路查。",
                "2. 增压目标正常但实际起不来，优先查进排气和增压控制。",
                "3. 如果限扭状态先触发，要先找触发限扭的上游原因，而不是只盯着动力表现。",
            ],
            repair_actions=[
                "1. 修复低压供油、轨压建立或高压侧泄漏问题。",
                "2. 修复进气、增压控制或泄漏问题。",
                "3. 排除触发限扭的原始故障，再复核动力恢复情况。",
            ],
            cautions=[
                "1. 动力不足最怕把轨压、增压、限扭三条线混着讲，现场会越查越散。",
                "2. 没看到数据流主线异常前，不要急着判高压泵、喷油器或增压器坏。",
            ],
        )

    def _build_generic_repair_fallback_content(
        self,
        *,
        summary_text: str,
        loaded_context: dict[str, Any],
    ) -> str:
        known = summary_text or "当前已具备继续排查的基础信息。"
        source_tips = self._extract_repair_source_tips(loaded_context)
        if not source_tips:
            source_tips = [
                "先从最稳定复现的故障现象下手。",
                "优先核对最基础的供电、搭铁和关键输入信号。",
                "确认基础数据异常后，再决定是否继续拆检总成。",
            ]
        numbered_tips = [f"{index + 1}. {tip}" for index, tip in enumerate(source_tips[:4])]
        return self._build_repair_guideline_content(
            fault_definition=f"结合当前已知情况：{known}。当前不是继续堆更多可能原因，而是先把问题收敛成可执行的诊断任务。",
            diagnosis_type="更像通用维修排故场景，先围绕现有资料主线做分步确认，再决定是否深入到线路或总成。",
            cause_groups=[
                "1. 基础条件异常：供电、搭铁、报码方向和关键输入条件不成立。",
                "2. 执行或系统本体异常：执行器、总成或对应机械链路存在真实故障。",
                "3. 反馈与控制异常：传感器、线束或控制策略让现象和真实故障不完全一致。",
            ],
            check_steps=numbered_tips,
            judgment_points=[
                "1. 先把最前面的基础项查实，再判断主问题是在控制侧、信号侧还是机械侧。",
                "2. 现象、报码和数据流一致时沿主线深挖；明显不一致时优先检查信号和线束。",
            ],
            repair_actions=[
                "1. 哪条主线先被证实，就先修哪条，不要同时拆很多系统。",
                "2. 先处理低成本、高概率故障点，再决定是否进入深层拆检。",
            ],
            cautions=[
                "1. 通用故障最怕答案看起来专业，但落不到先查什么、后查什么。",
                "2. 没有判断依据的“可能原因列表”参考价值很低，现场要以主线和判据为准。",
            ],
        )

    @staticmethod
    def _repair_field_text(field_values: dict[str, dict[str, Any]], key: str) -> str:
        item = field_values.get(key) or {}
        values = [str(value).strip() for value in (item.get("selected") or []) if str(value).strip()]
        text_value = str(item.get("text") or "").strip()
        if text_value:
            values.append(text_value)
        return "；".join(values)

    @staticmethod
    def _extract_repair_source_tips(loaded_context: dict[str, Any]) -> list[str]:
        tips: list[str] = []
        for entry in loaded_context.get("entries") or []:
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            for raw_piece in re.split(r"[\n。；]", content):
                piece = re.sub(r"^\s*(?:#{1,6}\s*|[-*]|\d+[.、）)])\s*", "", raw_piece).strip()
                if not piece:
                    continue
                if piece in {"输入信息", "还需补充", "补充信息"}:
                    continue
                if RepairKnowledgeFollowupAdapter._looks_like_textual_followup_item(piece):
                    continue
                if piece not in tips:
                    tips.append(piece)
                if len(tips) >= 3:
                    return tips
        return tips

    def _maybe_rewrite_repair_followup_message(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        full_messages: Sequence[Any] | None,
        content: Any,
        extra_metadata: dict[str, Any] | None,
    ) -> tuple[Any, dict[str, Any] | None]:
        if not self._is_repair_followup_answer_request(request):
            return content, extra_metadata
        if not isinstance(content, str):
            return content, extra_metadata

        normalized = RepairKnowledgeFollowupAdapter.normalize_user_facing_message(content)
        if self._is_repair_guideline_answer(normalized):
            return self._ensure_repair_guideline_salutation(normalized), extra_metadata
        return normalized, extra_metadata

    def _persist_synthetic_message_history(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        base_messages: Sequence[Any],
        user_prompt: str | None,
        content: str,
    ) -> None:
        from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart

        messages = list(base_messages)
        if user_prompt:
            messages.append(ModelRequest.user_text_prompt(user_prompt))
        messages.append(ModelResponse(parts=[TextPart(content=content)]))
        active_deps.message_history_store.save_serialized_history(
            session_id,
            self._serialize_history(messages),
        )

    def _try_build_synthetic_repair_followup_response(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        full_messages: Sequence[Any],
        serialized_history: str,
        content: Any,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse | None:
        if not isinstance(content, str):
            return None

        loaded_context = self._extract_loaded_repair_knowledge_context(full_messages)
        if not RepairKnowledgeFollowupAdapter.should_convert_to_followup(content, loaded_context):
            return None

        query = self._extract_latest_user_prompt(full_messages) or (request.message or "").strip()
        ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
            query=query,
            loaded_context=loaded_context,
            answer_text=content,
        )
        synthetic_history = self._build_synthetic_ask_user_history(
            full_messages=full_messages,
            ask_user=ask_user,
        )
        active_deps.deferred_state_store.save(
            session_id=session_id,
            state=RepairKnowledgeFollowupAdapter.build_deferred_state(
                tool_call_id=ask_user.tool_call_id,
                message_history_json=self._serialize_history(synthetic_history),
                query=query,
                ask_user=ask_user,
            ),
        )
        active_deps.tracer.trace(
            event_type="repair_knowledge_followup_synthesized",
            session_id=session_id,
            payload={"tool_call_id": ask_user.tool_call_id, "query": query},
        )
        return self._build_ask_user_response(
            ask_user=ask_user,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business="GENERAL_CHAT",
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    async def _try_build_synthetic_repair_followup_response_async(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        full_messages: Sequence[Any],
        serialized_history: str,
        content: Any,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse | None:
        if not isinstance(content, str):
            return None

        loaded_context = self._extract_loaded_repair_knowledge_context(full_messages)
        if not RepairKnowledgeFollowupAdapter.should_convert_to_followup(content, loaded_context):
            return None

        query = self._extract_latest_user_prompt(full_messages) or (request.message or "").strip()
        ask_user = await RepairKnowledgeFollowupAdapter.build_ask_user_question_async(
            query=query,
            loaded_context=loaded_context,
            answer_text=content,
        )
        synthetic_history = self._build_synthetic_ask_user_history(
            full_messages=full_messages,
            ask_user=ask_user,
        )
        active_deps.deferred_state_store.save(
            session_id=session_id,
            state=RepairKnowledgeFollowupAdapter.build_deferred_state(
                tool_call_id=ask_user.tool_call_id,
                message_history_json=self._serialize_history(synthetic_history),
                query=query,
                ask_user=ask_user,
            ),
        )
        active_deps.tracer.trace(
            event_type="repair_knowledge_followup_synthesized",
            session_id=session_id,
            payload={"tool_call_id": ask_user.tool_call_id, "query": query},
        )
        return self._build_ask_user_response(
            ask_user=ask_user,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business="GENERAL_CHAT",
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    def _prepare_run_state(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        error_as_response: bool,
    ) -> tuple[Sequence[Any] | ChatResponse | AgentRuntimeEvent | None, Any]:
        message_history: Sequence[Any] | None = None
        deferred_tool_results = None

        if request.ask_user_answer is not None:
            if not request.session_id:
                error = self._build_prepare_error(
                    as_response=error_as_response,
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    message="session_id is required when resuming a deferred ask_user_question call.",
                    error_code="ASK_USER_SESSION_REQUIRED",
                )
                return error, None

            resume_state = active_deps.deferred_state_store.load(
                session_id=session_id,
                tool_call_id=request.ask_user_answer.tool_call_id,
            )
            if resume_state is None:
                error = self._build_prepare_error(
                    as_response=error_as_response,
                    deps=active_deps,
                    request_id=request_id,
                    session_id=session_id,
                    message="Deferred ask_user_question state was not found for this session.",
                    error_code="DEFERRED_TOOL_STATE_NOT_FOUND",
                    detail=request.ask_user_answer.tool_call_id,
                )
                return error, None

            message_history = self._deserialize_history(resume_state.message_history_json)
            deferred_tool_results = self._build_deferred_results(request.ask_user_answer)
            resume_business = str((resume_state.payload or {}).get("resume_business") or "").strip().upper()
            if resume_business:
                if not isinstance(request.context, dict):
                    request.context = {}
                request.context[self._RESUME_BUSINESS_CONTEXT_KEY] = resume_business
        else:
            if self._should_reset_history(request):
                active_deps.message_history_store.save_serialized_history(session_id, "[]")
            else:
                serialized_history = active_deps.message_history_store.load_serialized_history(session_id)
                if serialized_history:
                    message_history = self._deserialize_history(serialized_history)

        return message_history, deferred_tool_results

    def _build_prepare_error(
        self,
        *,
        as_response: bool,
        deps: AgentRuntimeDeps,
        request_id: str,
        session_id: str,
        message: str,
        error_code: str,
        detail: str | None = None,
    ) -> ChatResponse | AgentRuntimeEvent:
        if as_response:
            return self._error_response(
                deps=deps,
                request_id=request_id,
                session_id=session_id,
                error_code=error_code,
                message=message,
                detail=detail,
            )

        return AgentRuntimeEvent(
            type=AgentEventType.ERROR,
            session_id=session_id,
            message=message,
            metadata={"request_id": request_id, "error_code": error_code, "detail": detail},
        )

    @staticmethod
    def _agent_supports_streaming(agent: Any) -> bool:
        from pydantic_ai.models.function import FunctionModel

        model = getattr(agent, "model", None)
        if isinstance(model, FunctionModel):
            return model.stream_function is not None
        return True

    def _finalize_stream_run_result(
        self,
        *,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        message_history: Sequence[Any] | None,
        serialized_history: str,
        output: Any,
        render_state: RepairRenderRuntimeState | None = None,
    ) -> tuple[ChatResponse | None, str]:
        from pydantic_ai import DeferredToolRequests

        active_deps.message_history_store.save_serialized_history(session_id, serialized_history)
        full_messages = self._deserialize_history(serialized_history)
        run_messages = self._current_run_messages(
            full_messages=full_messages,
            message_history=message_history,
        )

        if isinstance(output, DeferredToolRequests):
            ask_user = self._extract_ask_user_question(output)
            if ask_user is None:
                return None, ""
            ask_user = self._normalize_runtime_ask_user_question(
                ask_user=ask_user,
                request=request,
                full_messages=full_messages,
            )

            business = self._infer_business_from_messages(
                run_messages,
                request,
                fallback_messages=full_messages,
            )
            self._save_agent_ask_user_state(
                active_deps=active_deps,
                session_id=session_id,
                serialized_history=serialized_history,
                full_messages=full_messages,
                ask_user=ask_user,
                business=business,
                deferred_requests=output,
            )
            self._persist_case_context_after_agent_run(
                active_deps=active_deps,
                run_messages=run_messages,
                request=request,
                ask_user=ask_user,
                business=business,
            )
            return (
                self._build_ask_user_response(
                    ask_user=ask_user,
                    session_id=session_id,
                    request_id=request_id,
                    runtime_version=runtime_version,
                    business=business,
                    llm_observability=getattr(active_deps, "llm_observability", None),
                ),
                "",
            )

        synthetic_repair_followup = self._try_build_synthetic_repair_followup_response(
            request=request,
            active_deps=active_deps,
            full_messages=full_messages,
            serialized_history=serialized_history,
            content=output,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
        )
        if synthetic_repair_followup is not None:
            self._persist_case_context_after_agent_run(
                active_deps=active_deps,
                run_messages=run_messages,
                request=request,
                ask_user=synthetic_repair_followup.ask_user,
                business="GENERAL_CHAT",
            )
            return synthetic_repair_followup, ""

        response = self._try_extract_structured_response(
            request=request,
            active_deps=active_deps,
            messages=run_messages,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
        )
        if response is None:
            response = self._try_recover_standalone_param_response(
                request=request,
                active_deps=active_deps,
                messages=run_messages,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
            )
        if response is None:
            repair_knowledge_metadata = self._extract_repair_knowledge_metadata(full_messages)
            if render_state is not None:
                final_content, repair_knowledge_metadata = self._finalize_repair_rendered_content(
                    content=output,
                    extra_metadata=repair_knowledge_metadata,
                    render_state=render_state,
                )
            else:
                final_content, repair_knowledge_metadata = self._maybe_rewrite_repair_followup_message(
                    request=request,
                    active_deps=active_deps,
                    session_id=session_id,
                    full_messages=full_messages,
                    content=output,
                    extra_metadata=repair_knowledge_metadata,
                )
            response = self._build_message_response(
                content=final_content,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business=self._infer_message_business(
                    run_messages,
                    request,
                    fallback_messages=full_messages,
                ),
                extra_metadata=repair_knowledge_metadata,
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        self._persist_case_context_after_agent_run(
            active_deps=active_deps,
            run_messages=run_messages,
            request=request,
            ask_user=None,
            business=None,
        )

        if isinstance(output, str) and response.type == "message":
            normalized_full_content = response.content if isinstance(response.content, str) else ""
            return response, normalized_full_content
        return response, ""

    def _handle_guard_exceeded(
        self,
        *,
        exc: LoopGuardExceededError,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        message_history: Sequence[Any] | None,
        captured_messages: Sequence[Any] | None,
    ) -> GuardConvergenceResult:
        full_messages = self._sanitize_messages_after_guard_block(
            messages=list(captured_messages or message_history or []),
            tool_name=exc.tool_name,
        )
        serialized_history = self._serialize_history(full_messages) if full_messages else "[]"
        active_deps.message_history_store.save_serialized_history(session_id, serialized_history)
        run_messages = self._current_run_messages(
            full_messages=full_messages,
            message_history=message_history,
        )

        response = self._try_extract_structured_response(
            request=request,
            active_deps=active_deps,
            messages=run_messages,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            for_convergence=True,
        )
        if response is not None:
            self._persist_case_context_after_agent_run(
                active_deps=active_deps,
                run_messages=run_messages,
                request=request,
                ask_user=None,
                business=None,
            )
            mode = "ask_user_required" if response.type == "ask_user" else "best_effort_answer"
            return GuardConvergenceResult(
                response=self._decorate_guard_convergence_response(
                    response=response,
                    mode=mode,
                    exc=exc,
                    budget_snapshot=self._guard_budget_snapshot(active_deps),
                ),
                mode=mode,
            )

        ask_user_response = self._try_build_guard_ask_user_response(
            exc=exc,
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            full_messages=full_messages,
            run_messages=run_messages,
            serialized_history=serialized_history,
        )
        if ask_user_response is not None:
            return ask_user_response

        best_effort_response = self._try_build_guard_best_effort_response(
            exc=exc,
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            messages=run_messages,
            fallback_messages=full_messages,
        )
        if best_effort_response is not None:
            self._persist_case_context_after_agent_run(
                active_deps=active_deps,
                run_messages=run_messages,
                request=request,
                ask_user=None,
                business=None,
            )
            return GuardConvergenceResult(
                response=self._decorate_guard_convergence_response(
                    response=best_effort_response,
                    mode="best_effort_answer",
                    exc=exc,
                    budget_snapshot=self._guard_budget_snapshot(active_deps),
                ),
                mode="best_effort_answer",
            )

        insufficient = self._build_guard_insufficient_information_response(
            exc=exc,
            request=request,
            active_deps=active_deps,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            messages=run_messages,
            fallback_messages=full_messages,
        )
        self._persist_case_context_after_agent_run(
            active_deps=active_deps,
            run_messages=run_messages,
            request=request,
            ask_user=None,
            business=None,
        )
        return GuardConvergenceResult(
            response=self._decorate_guard_convergence_response(
                response=insufficient,
                mode="insufficient_information",
                exc=exc,
                budget_snapshot=self._guard_budget_snapshot(active_deps),
            ),
            mode="insufficient_information",
        )

    @staticmethod
    def _sanitize_messages_after_guard_block(
        *,
        messages: list[Any],
        tool_name: str,
    ) -> Sequence[Any]:
        if not messages:
            return messages

        from pydantic_ai.messages import ModelResponse, ToolCallPart

        last_message = messages[-1]
        if not isinstance(last_message, ModelResponse):
            return messages
        if any(isinstance(part, ToolCallPart) and part.tool_name == tool_name for part in last_message.parts):
            return messages[:-1]
        return messages

    @staticmethod
    def _guard_budget_snapshot(active_deps: AgentRuntimeDeps) -> dict[str, Any] | None:
        guard = getattr(active_deps, "loop_guard", None)
        if guard is None:
            return None
        return guard.snapshot().__dict__

    def _try_build_guard_ask_user_response(
        self,
        *,
        exc: LoopGuardExceededError,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        full_messages: Sequence[Any],
        run_messages: Sequence[Any],
        serialized_history: str,
    ) -> GuardConvergenceResult | None:
        if not self._can_still_ask_user(active_deps, exc):
            return None

        ask_user: AskUserQuestion | None = None
        business = self._infer_business_from_messages(
            run_messages,
            request,
            fallback_messages=full_messages,
        )

        parameter_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(run_messages, "query_parameters")
        if parameter_envelope is not None and parameter_envelope.get("status") == "need_clarify":
            ask_user = ParameterQueryResponseAdapter.build_ask_user_question(parameter_envelope)
            business = "PARAM_QUERY"

        if ask_user is None:
            fault_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(
                run_messages,
                "lookup_ecu_candidates",
            )
            if fault_envelope is not None and fault_envelope.get("status") == "need_clarify":
                ask_user = self._build_ask_user_from_clarify_envelope(
                    clarify_envelope=fault_envelope,
                    tool_call_prefix="fault_diag",
                    default_question="请选择对应 ECU",
                )
                business = "FAULT_DIAGNOSIS"

        if ask_user is None:
            loaded_context = self._extract_loaded_repair_knowledge_context(full_messages)
            if loaded_context is not None:
                query = self._extract_latest_user_prompt(full_messages) or (request.message or "").strip()
                answer_text = loaded_context.get("llm_context") or "\n".join(
                    str(item.get("content") or "") for item in (loaded_context.get("entries") or [])
                )
                ask_user = RepairKnowledgeFollowupAdapter.build_ask_user_question(
                    query=query,
                    loaded_context=loaded_context,
                    answer_text=answer_text,
                )
                business = "GENERAL_CHAT"

        if ask_user is None:
            return None

        active_deps.deferred_state_store.save(
            session_id=session_id,
            state=DeferredState(
                tool_call_id=ask_user.tool_call_id,
                tool_name="ask_user_question",
                message_history_json=serialized_history,
                payload=self._build_ask_user_deferred_payload(ask_user=ask_user, business=business),
            ),
        )
        self._persist_case_context_after_agent_run(
            active_deps=active_deps,
            run_messages=run_messages,
            request=request,
            ask_user=ask_user,
            business=business,
        )
        response = self._build_ask_user_response(
            ask_user=ask_user,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business=business,
            llm_observability=getattr(active_deps, "llm_observability", None),
        )
        return GuardConvergenceResult(
            response=self._decorate_guard_convergence_response(
                response=response,
                mode="ask_user_required",
                exc=exc,
                budget_snapshot=self._guard_budget_snapshot(active_deps),
            ),
            mode="ask_user_required",
        )

    def _try_build_guard_best_effort_response(
        self,
        *,
        exc: LoopGuardExceededError,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        messages: Sequence[Any],
        fallback_messages: Sequence[Any],
    ) -> ChatResponse | None:
        del exc
        parameter_response = self._try_extract_param_response(
            active_deps=active_deps,
            messages=messages,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            for_convergence=True,
        )
        if parameter_response is not None:
            return parameter_response

        fault_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "dtc_diagnosis")
        if fault_envelope is None:
            fault_envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "lookup_ecu_candidates")
        if fault_envelope is not None:
            fault_response = self._build_best_effort_fault_diagnosis_response(
                active_deps=active_deps,
                envelope=fault_envelope,
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
            )
            if fault_response is not None:
                return fault_response

        loaded_context = self._extract_loaded_repair_knowledge_context(fallback_messages)
        if loaded_context is not None:
            source_refs = list(loaded_context.get("source_refs") or [])[:3]
            primary_source = loaded_context.get("primary_source") or {}
            primary_title = primary_source.get("title")
            return self._build_message_response(
                content="已加载相关维修资料，但本轮工具调用已达到限制。可先基于现有线索完成当前检查，再决定是否进入下一轮诊断。",
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="GENERAL_CHAT",
                extra_metadata={
                    "repair_knowledge_sources": source_refs,
                    "repair_knowledge_primary_title": primary_title,
                },
                llm_observability=getattr(active_deps, "llm_observability", None),
            )

        business = self._infer_business_from_messages(
            messages,
            request,
            fallback_messages=fallback_messages,
        )
        if business == "FAULT_DIAGNOSIS":
            return self._build_message_response(
                content="已达到本轮工具调用限制。现有诊断线索只能支撑初步判断，本轮先停止扩展。",
                session_id=session_id,
                request_id=request_id,
                runtime_version=runtime_version,
                business="FAULT_DIAGNOSIS",
                llm_observability=getattr(active_deps, "llm_observability", None),
            )
        return None

    def _build_guard_insufficient_information_response(
        self,
        *,
        exc: LoopGuardExceededError,
        request: ChatRequest,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        messages: Sequence[Any],
        fallback_messages: Sequence[Any],
    ) -> ChatResponse:
        del exc
        business = self._infer_message_business(
            messages,
            request,
            fallback_messages=fallback_messages,
        )
        if business == "PARAM_QUERY":
            content = "已达到本轮工具调用限制，现有证据还不足以稳定给出参数结果，本轮先停止扩展。"
        elif business == "FAULT_DIAGNOSIS":
            content = "已达到本轮工具调用限制，现有证据还不足以稳定完成诊断，本轮先停止扩展。"
        else:
            content = "已达到本轮工具调用限制，现有证据还不足以继续稳定推进，本轮先停止扩展。"

        return self._build_message_response(
            content=content,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business=business,
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    @staticmethod
    def _can_still_ask_user(active_deps: AgentRuntimeDeps, exc: LoopGuardExceededError) -> bool:
        if exc.error_code == "LOOP_GUARD_MAX_ASK_USER_CALLS":
            return False
        guard = getattr(active_deps, "loop_guard", None)
        if guard is None:
            return True
        remaining = guard.snapshot().remaining_ask_user_calls
        return remaining is None or remaining > 0

    @staticmethod
    def _build_ask_user_from_clarify_envelope(
        *,
        clarify_envelope: dict[str, Any],
        tool_call_prefix: str,
        default_question: str,
    ) -> AskUserQuestion:
        clarify = clarify_envelope.get("clarify") or {}
        options = [AskUserOption.model_validate(item) for item in (clarify.get("options") or [])]
        input_type = AskUserInputType.SINGLE_SELECT if options else AskUserInputType.TEXT
        clarify_context = clarify.get("context") or {}
        allow_free_input = bool(clarify_context.get("allow_free_input", False)) and not options
        ask_user = AskUserQuestion(
            tool_call_id=f"{tool_call_prefix}_{uuid4().hex}",
            question=clarify.get("question") or default_question,
            input_type=input_type,
            options=options,
            allow_free_input=allow_free_input,
            input_hint=clarify_context.get("input_hint") or ("也可以直接输入补充信息" if allow_free_input else None),
            context=clarify_context,
        )
        form = build_single_field_form(
            form_id=f"{tool_call_prefix}_form_{ask_user.tool_call_id}",
            title="请确认关键信息",
            description="确认后继续当前流程。",
            ask_reason=clarify_context.get("message") or "当前仍需要一个确定的补充条件。",
            field_key=str(clarify_context.get("facet") or "clarify_choice"),
            field_label=ask_user.question,
            input_type=input_type,
            options=options,
            allow_free_input=allow_free_input,
            input_hint=ask_user.input_hint,
            auto_submit_single_select=True,
        )
        return attach_form_to_ask_user(
            ask_user,
            form=form,
            scene=str(clarify_context.get("scene") or "generic_ask_user"),
        )

    def _build_best_effort_fault_diagnosis_response(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        envelope: dict[str, Any],
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse | None:
        data = envelope.get("data") or {}
        message = data.get("message")
        if not message:
            return None
        return self._build_message_response(
            content=message,
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business="FAULT_DIAGNOSIS",
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    def _build_fault_diagnosis_message_response(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        envelope: dict[str, Any],
        session_id: str,
        request_id: str,
        runtime_version: str | None,
    ) -> ChatResponse:
        data = envelope.get("data") or {}
        metadata = {
            "fault_diagnosis": {
                "state": data.get("state"),
                "fault_code": data.get("fault_code"),
                "ecu_model": data.get("ecu_model"),
                "report_url": data.get("report_url"),
                "error": data.get("error"),
            }
        }
        return self._build_message_response(
            content=data.get("message") or "当前诊断线索暂时不足以继续稳定收敛，本轮先停止扩展。",
            session_id=session_id,
            request_id=request_id,
            runtime_version=runtime_version,
            business="FAULT_DIAGNOSIS",
            extra_metadata=metadata,
            llm_observability=getattr(active_deps, "llm_observability", None),
        )

    @staticmethod
    def _decorate_guard_convergence_response(
        *,
        response: ChatResponse,
        mode: str,
        exc: LoopGuardExceededError,
        budget_snapshot: dict[str, Any] | None,
    ) -> ChatResponse:
        metadata = dict(response.metadata)
        metadata.update(
            {
                "convergence_reason": "loop_guard",
                "convergence_mode": mode,
                "guard_error_code": exc.error_code,
                "guard_tool_name": exc.tool_name,
            }
        )
        if budget_snapshot is not None:
            metadata["guard_budget"] = budget_snapshot
        return response.model_copy(update={"metadata": metadata})

    @staticmethod
    def _build_deferred_results(answer: AskUserAnswer):
        from pydantic_ai import DeferredToolResults

        payload = {"answer": answer.answer}
        if answer.metadata:
            payload["metadata"] = answer.metadata
            selection_payload = answer.metadata.get("selection_payload")
            if selection_payload is not None:
                payload["selection_payload"] = selection_payload

        return DeferredToolResults(calls={answer.tool_call_id: payload})

    @staticmethod
    def _deserialize_history(serialized_history: str) -> Sequence[Any]:
        from pydantic_ai import ModelMessagesTypeAdapter

        return ModelMessagesTypeAdapter.validate_json(serialized_history)

    @staticmethod
    def _serialize_history(messages: Sequence[Any]) -> str:
        from pydantic_ai import ModelMessagesTypeAdapter

        return ModelMessagesTypeAdapter.dump_json(list(messages)).decode("utf-8")

    @staticmethod
    def _extract_ask_user_question(deferred_requests: Any) -> AskUserQuestion | None:
        for call in deferred_requests.calls:
            metadata = deferred_requests.metadata.get(call.tool_call_id, {})
            deferred_as = str(metadata.get("deferred_as") or "").strip().lower()
            if call.tool_name != "ask_user_question" and deferred_as != "ask_user_question":
                continue

            args = call.args if isinstance(call.args, dict) else {}
            payload = {**args, **metadata}

            input_type_value = payload.get("input_type", AskUserInputType.TEXT.value)
            try:
                input_type = AskUserInputType(input_type_value)
            except ValueError:
                input_type = AskUserInputType.TEXT

            ask_user = AskUserQuestion(
                tool_call_id=call.tool_call_id,
                question=payload.get("question", "请补充必要信息"),
                input_type=input_type,
                options=[AskUserOption.model_validate(item) for item in payload.get("options", [])],
                allow_free_input=bool(payload.get("allow_free_input", False)),
                input_hint=payload.get("input_hint"),
                unit=payload.get("unit"),
                reference_range=payload.get("reference_range"),
                context=payload.get("context") or {},
            )
            return normalize_ask_user_question_v2(ask_user)

        return None

    @staticmethod
    def _extract_parameter_query_deferred_query(deferred_requests: Any) -> str | None:
        for call in deferred_requests.calls:
            if call.tool_name != "query_parameters":
                continue
            metadata = deferred_requests.metadata.get(call.tool_call_id, {})
            if str(metadata.get("deferred_tool_name") or "").strip() != PARAM_QUERY_DEFERRED_TOOL_NAME:
                continue
            query = str(metadata.get("query") or "").strip()
            if query:
                return query
        return None

    def _save_agent_ask_user_state(
        self,
        *,
        active_deps: AgentRuntimeDeps,
        session_id: str,
        serialized_history: str,
        full_messages: Sequence[Any],
        ask_user: AskUserQuestion,
        business: str | None,
        deferred_requests: Any,
    ) -> None:
        message_history_json = serialized_history
        if self._extract_parameter_query_deferred_query(deferred_requests) is not None:
            synthetic_history = self._build_synthetic_ask_user_history(
                full_messages=full_messages,
                ask_user=ask_user,
            )
            message_history_json = self._serialize_history(synthetic_history)

        active_deps.deferred_state_store.save(
            session_id=session_id,
            state=DeferredState(
                tool_call_id=ask_user.tool_call_id,
                tool_name="ask_user_question",
                message_history_json=message_history_json,
                payload=self._build_ask_user_deferred_payload(ask_user=ask_user, business=business),
            ),
        )

    @staticmethod
    def _build_ask_user_deferred_payload(
        *,
        ask_user: AskUserQuestion,
        business: str | None,
    ) -> dict[str, Any]:
        payload = ask_user.model_dump(mode="json")
        if business:
            payload["resume_business"] = business
        return payload

    @staticmethod
    def _build_ask_user_response(
        *,
        ask_user: AskUserQuestion,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        business: str = "AGENT_LOOP",
        llm_observability: dict[str, Any] | None = None,
    ) -> ChatResponse:
        ask_user = normalize_ask_user_question_v2(ask_user)
        return ChatResponse(
            type="ask_user",
            content=ask_user.model_dump(mode="json"),
            session_id=session_id,
            request_id=request_id,
            business=business,
            need_clarify=True,
            clarify_facet="ask_user_question",
            clarify_options=[
                ClarifyOption(
                    key=option.key,
                    label=option.label,
                    description=option.description,
                    selection_payload=option.selection_payload.model_dump(mode="json"),
                )
                for option in ask_user.options
            ],
            metadata=AgentLoopService._merge_response_metadata(
                base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
                llm_observability=llm_observability,
                extra={"tool_call_id": ask_user.tool_call_id},
            ),
            ask_user=ask_user,
        )

    @staticmethod
    def _build_message_response(
        *,
        content: Any,
        session_id: str,
        request_id: str,
        runtime_version: str | None,
        business: str = "AGENT_LOOP",
        extra_metadata: dict[str, Any] | None = None,
        llm_observability: dict[str, Any] | None = None,
    ) -> ChatResponse:
        metadata = AgentLoopService._merge_response_metadata(
            base={"runtime": "pydantic_ai", "runtime_version": runtime_version},
            llm_observability=llm_observability,
            extra=extra_metadata,
        )
        normalized_content = AgentLoopService._normalize_repair_knowledge_answer_content(
            content=content,
            metadata=metadata,
        )
        return ChatResponse(
            type="message",
            content=normalized_content,
            session_id=session_id,
            request_id=request_id,
            business=business,
            metadata=metadata,
        )

    @staticmethod
    def _response_stream_full_content(response: ChatResponse | None) -> str:
        if response is None:
            return ""
        if response.type not in {"message", "text"}:
            return ""
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            value = content.get("message")
            if isinstance(value, str):
                return value
        return ""

    def _error_response(
        self,
        deps: AgentRuntimeDeps,
        request_id: str,
        session_id: str,
        error_code: str,
        message: str,
        detail: str | None = None,
    ) -> ChatResponse:
        deps.tracer.trace(
            event_type="agent_loop_error_response",
            session_id=session_id,
            detail=detail or message,
        )
        return ChatResponse(
            type="error",
            content={
                "message": message,
                "error_code": error_code,
                "reason": detail,
            },
            session_id=session_id,
            request_id=request_id,
            business="AGENT_LOOP",
            metadata=self._merge_response_metadata(
                base={},
                llm_observability=getattr(deps, "llm_observability", None),
                extra={"error_code": error_code},
            ),
        )

    @staticmethod
    def _public_runtime_error_message(exc: Exception) -> str:
        try:
            from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
        except Exception:
            ModelAPIError = ModelHTTPError = UnexpectedModelBehavior = tuple()  # type: ignore[assignment]

        if isinstance(exc, ModelHTTPError):
            return "模型服务暂时不可用，请稍后重试。"
        if isinstance(exc, ModelAPIError):
            return "模型服务连接失败，请稍后重试。"
        if isinstance(exc, UnexpectedModelBehavior):
            return "模型服务返回异常结果，请稍后重试。"
        return "系统处理请求时发生错误，请稍后重试。"

    @staticmethod
    def _should_reset_history(request: ChatRequest) -> bool:
        return bool(request.lifecycle_check and request.lifecycle_check.user_confirmed_switch)

    @staticmethod
    def _extract_repair_knowledge_metadata(messages: Sequence[Any] | None) -> dict[str, Any]:
        if not messages:
            return {}

        envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "get_repair_knowledge_context")
        if not envelope:
            envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "lookup_repair_knowledge")
        if not envelope or envelope.get("status") != "ok":
            return {}

        data = envelope.get("data") or {}
        if not data.get("loaded") and not data.get("matched"):
            return {}

        source_refs = data.get("source_refs") or []
        if not isinstance(source_refs, list) or not source_refs:
            return {}

        return {
            "repair_knowledge_sources": source_refs,
            "repair_knowledge_primary_title": (data.get("primary_source") or {}).get("title"),
        }

    @staticmethod
    def _extract_loaded_repair_knowledge_context(messages: Sequence[Any] | None) -> dict[str, Any] | None:
        if not messages:
            return None
        envelope = DocSearchResponseAdapter.extract_latest_tool_envelope(messages, "get_repair_knowledge_context")
        if not envelope or envelope.get("status") != "ok":
            return None
        data = envelope.get("data") or {}
        if not data.get("loaded"):
            return None
        return data

    @staticmethod
    def _build_synthetic_ask_user_history(
        *,
        full_messages: Sequence[Any],
        ask_user: AskUserQuestion,
    ) -> Sequence[Any]:
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

        base_messages = list(full_messages)
        if base_messages:
            last_message = base_messages[-1]
            if isinstance(last_message, ModelResponse) and any(isinstance(part, TextPart) for part in last_message.parts):
                base_messages = base_messages[:-1]
            elif isinstance(last_message, ModelResponse):
                retained_parts = [
                    part
                    for part in last_message.parts
                    if not (
                        isinstance(part, ToolCallPart)
                        and part.tool_call_id == ask_user.tool_call_id
                    )
                ]
                if len(retained_parts) != len(last_message.parts):
                    if retained_parts:
                        base_messages[-1] = last_message.model_copy(update={"parts": retained_parts})
                    else:
                        base_messages = base_messages[:-1]

        base_messages.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": ask_user.question,
                            "input_type": ask_user.input_type.value,
                            "options": [option.model_dump(mode="json") for option in ask_user.options],
                            "allow_free_input": ask_user.allow_free_input,
                            "input_hint": ask_user.input_hint,
                            "unit": ask_user.unit,
                            "reference_range": ask_user.reference_range,
                            "context": ask_user.context,
                        },
                        tool_call_id=ask_user.tool_call_id,
                    )
                ]
            )
        )
        return base_messages

    @staticmethod
    def _is_repair_followup_answer_request(request: ChatRequest) -> bool:
        payload = request.ask_user_answer.answer if request.ask_user_answer is not None else None
        return AgentLoopService._is_repair_followup_payload(payload)

    @staticmethod
    def _is_repair_followup_payload(payload: Any) -> bool:
        return isinstance(payload, dict) and payload.get("scene") == "repair_knowledge_followup"

    @staticmethod
    def _is_repair_guideline_answer(content: str) -> bool:
        normalized = str(content or "").strip()
        required_sections = (
            "### 故障定义",
            "### 当前更像哪一型",
            "### 分步检查",
            "### 维修处理",
        )
        return all(section in normalized for section in required_sections) and "老哥，" in normalized

    @staticmethod
    def _ensure_repair_guideline_salutation(content: str) -> str:
        normalized = str(content or "").strip()
        prefix = "### 故障定义\n"
        if not normalized.startswith(prefix):
            return normalized
        body = normalized[len(prefix):]
        if body.startswith("老哥，"):
            return normalized
        return f"{prefix}老哥，{body}"

    @staticmethod
    def _normalize_repair_knowledge_answer_content(
        *,
        content: Any,
        metadata: dict[str, Any] | None,
    ) -> Any:
        if not isinstance(content, str):
            return content

        text = RepairKnowledgeFollowupAdapter.normalize_user_facing_message(content.lstrip())
        if not metadata or not metadata.get("repair_knowledge_sources"):
            return text
        heading_match = re.search(r"(^|\n)\s{0,3}#{2,6}\s+\S", text)
        if heading_match is None:
            return text

        heading_start = heading_match.start()
        if heading_match.group(1):
            heading_start += len(heading_match.group(1))

        if heading_start <= 0:
            return text

        return text[heading_start:].lstrip()
