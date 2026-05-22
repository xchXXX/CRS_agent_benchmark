"""Persist loop-oriented admin logs for task/run/event views."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from threading import Lock
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.agent.observability.tracer import LoopTraceEntry
from app.legacy.models.database import ChatRunEventLog, ChatRunLog, ChatTaskLog
from app.schemas.chat import ChatRequest, ChatResponse


logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATUSES = {"completed", "guard_stopped", "failed", "switched"}
_TABLES_READY_BINDS: set[int] = set()
_TABLES_LOCK = Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_preview(value: Any, max_length: int = 180) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        normalized = value.strip()
    elif isinstance(value, dict):
        normalized = str(value.get("message") or value.get("question") or json.dumps(value, ensure_ascii=False))
    else:
        normalized = str(value)
    normalized = normalized.replace("\n", " ").replace("\r", " ").strip()
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}…"


def _compact_value(value: Any, *, max_string: int = 240, max_items: int = 8) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_string else f"{value[: max_string - 1]}…"
    if isinstance(value, list):
        compacted = [_compact_value(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            compacted.append(f"...({len(value) - max_items} more)")
        return compacted
    if isinstance(value, dict):
        items = list(value.items())[:max_items]
        compacted = {
            str(key): _compact_value(item, max_string=max_string, max_items=max_items) for key, item in items
        }
        if len(value) > max_items:
            compacted["..."] = f"{len(value) - max_items} more"
        return compacted
    return value


def _response_payload(response: ChatResponse) -> dict[str, Any]:
    return response.model_dump(mode="json")


def _response_preview(response: ChatResponse) -> str:
    if response.type == "ask_user" and response.ask_user is not None:
        return _safe_preview(response.ask_user.question)
    return _safe_preview(response.content)


def _extract_missing_fields(response: ChatResponse) -> list[str]:
    if response.ask_user is None:
        return []
    context = response.ask_user.context or {}
    missing = context.get("missing_field_keys")
    if isinstance(missing, list):
        return [str(item).strip() for item in missing if str(item).strip()]
    field_groups = context.get("field_groups")
    if isinstance(field_groups, list):
        keys: list[str] = []
        for group in field_groups:
            if not isinstance(group, dict):
                continue
            key = str(group.get("field_key") or "").strip()
            if key:
                keys.append(key)
        return keys
    return []


def _extract_error(response: ChatResponse) -> tuple[str | None, str | None]:
    if response.type != "error":
        return None, None
    payload = response.content if isinstance(response.content, dict) else {"message": str(response.content)}
    return str(payload.get("error_code") or "error"), _safe_preview(payload.get("message"), max_length=500)


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.00000001"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _safe_optional_string(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized[:max_length]


def _extract_llm_metadata(response: ChatResponse) -> dict[str, Any]:
    metadata = response.metadata or {}
    llm = metadata.get("llm")
    if not isinstance(llm, dict):
        return {}
    usage = llm.get("aggregate_usage") if isinstance(llm.get("aggregate_usage"), dict) else llm.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "model_provider": _safe_optional_string(llm.get("provider_name"), 80),
        "model_name": _safe_optional_string(llm.get("model_name"), 160),
        "llm_call_count": _safe_int(llm.get("call_count") or (len(llm.get("calls")) if isinstance(llm.get("calls"), list) else 0)),
        "llm_elapsed_ms": _safe_optional_int(llm.get("aggregate_llm_elapsed_ms") or llm.get("llm_elapsed_ms")),
        "llm_first_response_ms": _safe_optional_int(
            llm.get("aggregate_first_response_ms")
            if llm.get("aggregate_first_response_ms") is not None
            else llm.get("first_response_ms")
        ),
        "llm_request_count": _safe_int(usage.get("request_count")),
        "input_token_count": _safe_int(usage.get("input_tokens")),
        "output_token_count": _safe_int(usage.get("output_tokens")),
        "total_token_count": _safe_int(usage.get("total_tokens")),
        "reasoning_token_count": _safe_int(usage.get("reasoning_tokens")),
        "estimated_cost_usd": _safe_decimal(
            llm.get("aggregate_estimated_cost_usd")
            if llm.get("aggregate_estimated_cost_usd") is not None
            else llm.get("estimated_cost_usd")
        ),
    }


def _resolve_run_status(response: ChatResponse) -> tuple[str, str, str | None]:
    metadata = response.metadata or {}
    if response.type == "ask_user":
        return "waiting_user", "ask_user", str(metadata.get("convergence_mode") or "ask_user")
    if response.type == "error":
        return "failed", "runtime_error", str(metadata.get("convergence_mode") or "error")
    if metadata.get("convergence_reason") == "loop_guard" or metadata.get("guard_error_code"):
        return "guard_stopped", "loop_guard", str(metadata.get("convergence_mode") or "loop_guard")
    return "completed", "direct_answer", str(metadata.get("convergence_mode") or "direct_answer")


def _resolve_trigger_type(request: ChatRequest) -> str:
    if request.ask_user_answer is not None:
        return "ask_user_resume"
    if request.lifecycle_check and request.lifecycle_check.user_confirmed_switch:
        return "user_switch"
    return "user_message"


def _resolve_phase(event_type: str) -> str:
    lowered = event_type.lower()
    if "request_scope" in lowered or "run_start" in lowered or "stream_start" in lowered:
        return "request"
    if "ask_user" in lowered:
        return "ask_user"
    if "guard" in lowered:
        return "guard"
    if lowered.startswith("circuit_"):
        return "tool"
    if "tool" in lowered:
        return "tool"
    if "error" in lowered:
        return "error"
    if "ready" in lowered or "done" in lowered:
        return "answer"
    return "runtime"


def _resolve_event_summary(event_type: str, payload: dict[str, Any], detail: str | None) -> str:
    tool_name = str(payload.get("tool_name") or "").strip()
    mapping = {
        "agent_loop_request_scope": "请求进入 loop",
        "agent_loop_stream_request_scope": "流式请求进入 loop",
        "intent_router_decision": "完成入口意图判定",
        "agent_loop_run_start": "开始执行",
        "agent_loop_stream_start": "开始流式执行",
        "agent_loop_run_done": "本轮执行结束",
        "agent_loop_ask_user": "生成 ask_user",
        "agent_loop_guard_before_tool_call": f"准备调用工具 {tool_name}".strip(),
        "agent_loop_guard_after_tool_call": f"工具调用完成 {tool_name}".strip(),
        "agent_loop_guard_blocked_before_tool_call": f"工具调用被拦截 {tool_name}".strip(),
        "agent_loop_guard_blocked_after_tool_call": f"工具结果被拦截 {tool_name}".strip(),
        "agent_loop_guard_exceeded": "达到 loop guard 限制",
        "agent_loop_error": "运行时异常",
        "agent_loop_error_response": "生成错误响应",
        "repair_answer_gate_ask_user": "回答审查阶段触发 ask_user",
        "repair_answer_gate_review_ask_user": "回答审查改为 ask_user",
        "repair_answer_gate_ready": "通过回答审查",
        "repair_answer_gate_review_blocked_ready": "回答审查阻止直接回答",
        "circuit_body_search_started": "开始电路图内搜索",
        "circuit_body_search_skipped": "跳过电路图内搜索",
        "circuit_body_source_docs_resolved": "完成电路图解析文档匹配",
        "circuit_body_source_docs_resolve_failed": "电路图解析文档匹配失败",
        "circuit_candidate_docs_search_skipped": "跳过电路图候选文档搜索",
        "circuit_candidate_docs_search_failed": "电路图候选文档搜索失败",
        "circuit_candidate_docs_searched": "完成电路图候选文档搜索",
        "circuit_body_doc_search_started": "开始单文档图内搜索",
        "circuit_body_doc_searched": "完成单文档图内搜索",
        "circuit_body_hit_rerank_skipped": "跳过图内候选排序",
        "circuit_body_hit_rerank_failed": "图内候选排序失败",
        "circuit_body_hit_reranked": "完成图内候选排序",
        "circuit_preview_token_skipped": "跳过局部预览 token 生成",
        "circuit_preview_token_failed": "局部预览 token 生成失败",
        "circuit_preview_token_created": "完成局部预览 token 生成",
        "circuit_body_search_completed": "完成电路图内搜索",
        "circuit_body_search_enhanced": "完成资料搜索结果增强",
        "circuit_body_search_enhance_failed": "资料搜索结果增强失败",
    }
    summary = mapping.get(event_type)
    if summary:
        return summary
    if tool_name:
        return f"{event_type} · {tool_name}"
    return detail or event_type


def _tool_summary(trace_entries: Iterable[LoopTraceEntry]) -> tuple[list[str], int, int]:
    executed = [entry for entry in trace_entries if entry.event_type == "agent_loop_guard_after_tool_call"]
    tool_names = [str(entry.payload.get("tool_name") or "").strip() for entry in executed]
    tool_names = [name for name in tool_names if name]
    external_count = sum(1 for entry in executed if str(entry.payload.get("tool_category") or "") == "external")
    return tool_names, len(executed), external_count


class AgentTaskLogService:
    def __init__(self, session_factory: Any):
        self._session_factory = session_factory

    def persist_interaction(
        self,
        *,
        request: ChatRequest,
        response: ChatResponse,
        user_id: int | None,
        trace_entries: list[LoopTraceEntry],
        elapsed_ms: int,
        transport: str,
    ) -> None:
        if self._session_factory is None:
            return

        db = self._session_factory()
        if not all(hasattr(db, attr) for attr in ("get_bind", "add", "commit")):
            if hasattr(db, "close"):
                db.close()
            return
        try:
            self.ensure_tables(db)
            now = _utcnow()
            task = self._resolve_task(db=db, request=request, response=response, user_id=user_id, now=now)
            tool_names, tool_call_count, external_tool_call_count = _tool_summary(trace_entries)
            run = self._build_run_log(
                task=task,
                request=request,
                response=response,
                user_id=user_id,
                elapsed_ms=elapsed_ms,
                transport=transport,
                tool_names=tool_names,
                tool_call_count=tool_call_count,
                external_tool_call_count=external_tool_call_count,
                now=now,
            )
            db.add(run)
            db.add_all(
                self._build_event_logs(
                    task=task,
                    run=run,
                    request=request,
                    response=response,
                    trace_entries=trace_entries,
                )
            )
            self._apply_run_to_task(
                task=task,
                request=request,
                response=response,
                run=run,
                tool_names=tool_names,
                tool_call_count=tool_call_count,
                external_tool_call_count=external_tool_call_count,
                now=now,
            )
            db.add(task)
            db.commit()
        except Exception as exc:
            logger.warning("Persist agent admin log failed: %s", exc, exc_info=True)
            if hasattr(db, "rollback"):
                db.rollback()
        finally:
            if hasattr(db, "close"):
                db.close()

    @staticmethod
    def ensure_tables(db: Session) -> None:
        bind = db.get_bind()
        bind_key = id(getattr(bind, "engine", bind))
        if bind_key in _TABLES_READY_BINDS:
            return
        with _TABLES_LOCK:
            if bind_key in _TABLES_READY_BINDS:
                return
            ChatTaskLog.__table__.create(bind=bind, checkfirst=True)
            ChatRunLog.__table__.create(bind=bind, checkfirst=True)
            ChatRunEventLog.__table__.create(bind=bind, checkfirst=True)
            AgentTaskLogService._ensure_chat_run_log_columns(bind)
            _TABLES_READY_BINDS.add(bind_key)

    @staticmethod
    def _ensure_chat_run_log_columns(bind: Any) -> None:
        dialect = bind.dialect.name
        if dialect not in {"sqlite", "mysql"}:
            return
        existing_columns = {column["name"] for column in inspect(bind).get_columns(ChatRunLog.__tablename__)}
        column_sql = {
            "model_provider": "VARCHAR(80)",
            "model_name": "VARCHAR(160)",
            "llm_call_count": "INTEGER DEFAULT 0",
            "llm_elapsed_ms": "INTEGER",
            "llm_first_response_ms": "INTEGER",
            "llm_request_count": "INTEGER DEFAULT 0",
            "input_token_count": "INTEGER DEFAULT 0",
            "output_token_count": "INTEGER DEFAULT 0",
            "total_token_count": "INTEGER DEFAULT 0",
            "reasoning_token_count": "INTEGER DEFAULT 0",
            "estimated_cost_usd": "NUMERIC(12, 8)",
        }
        with bind.begin() as connection:
            for column_name, ddl_type in column_sql.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(f"ALTER TABLE {ChatRunLog.__tablename__} ADD COLUMN {column_name} {ddl_type}")
                )

    @staticmethod
    def _resolve_task(
        *,
        db: Session,
        request: ChatRequest,
        response: ChatResponse,
        user_id: int | None,
        now: datetime,
    ) -> ChatTaskLog:
        latest = (
            db.query(ChatTaskLog)
            .filter(ChatTaskLog.session_id == response.session_id)
            .order_by(ChatTaskLog.created_at.desc(), ChatTaskLog.id.desc())
            .first()
        )

        replaces_task_id: str | None = None
        if request.lifecycle_check and request.lifecycle_check.user_confirmed_switch:
            if latest is not None and latest.task_status not in _TERMINAL_TASK_STATUSES:
                latest.task_status = "switched"
                latest.end_reason = "user_switched"
                latest.finished_at = now
                replaces_task_id = latest.task_id
            latest = None

        if latest is not None and request.ask_user_answer is not None and latest.task_status == "waiting_user":
            return latest

        if latest is not None and latest.task_status not in _TERMINAL_TASK_STATUSES:
            return latest

        task = ChatTaskLog(
            task_id=uuid4().hex,
            session_id=response.session_id,
            user_id=user_id,
            client_type=request.client_type or "web",
            root_question=(request.message or "").strip(),
            latest_user_message=(request.message or "").strip() or None,
            business_type=response.business,
            task_status="completed",
            replaces_task_id=replaces_task_id,
            first_request_id=response.request_id,
            last_request_id=response.request_id,
            started_at=now,
        )
        if latest is not None and replaces_task_id:
            latest.replaced_by_task_id = task.task_id
        return task

    @staticmethod
    def _build_run_log(
        *,
        task: ChatTaskLog,
        request: ChatRequest,
        response: ChatResponse,
        user_id: int | None,
        elapsed_ms: int,
        transport: str,
        tool_names: list[str],
        tool_call_count: int,
        external_tool_call_count: int,
        now: datetime,
    ) -> ChatRunLog:
        run_status, end_reason, convergence_mode = _resolve_run_status(response)
        error_type, error_message = _extract_error(response)
        ask_user_question = response.ask_user.question if response.ask_user is not None else None
        missing_fields = _extract_missing_fields(response)
        llm = _extract_llm_metadata(response)

        return ChatRunLog(
            run_id=uuid4().hex,
            task_id=task.task_id,
            session_id=response.session_id,
            request_id=response.request_id or uuid4().hex,
            user_id=user_id,
            client_type=request.client_type or "web",
            request_mode=request.mode,
            transport=transport,
            sequence_no=int(task.run_count or 0) + 1,
            trigger_type=_resolve_trigger_type(request),
            input_message=(request.message or "").strip() or None,
            ask_user_answer_summary=_safe_preview(request.ask_user_answer.answer) if request.ask_user_answer is not None else None,
            business_type=response.business,
            run_status=run_status,
            end_reason=end_reason,
            convergence_mode=convergence_mode,
            guard_error_code=str((response.metadata or {}).get("guard_error_code") or "") or None,
            response_type=response.type,
            response_preview=_response_preview(response),
            response_payload=_compact_value(_response_payload(response)),
            response_metadata=_compact_value(response.metadata or {}),
            ask_user_question=ask_user_question,
            missing_fields=missing_fields,
            ask_user_count=1 if response.type == "ask_user" else 0,
            tool_call_count=tool_call_count,
            external_tool_call_count=external_tool_call_count,
            tool_names=tool_names,
            model_provider=llm.get("model_provider"),
            model_name=llm.get("model_name"),
            llm_call_count=llm.get("llm_call_count", 0),
            llm_elapsed_ms=llm.get("llm_elapsed_ms"),
            llm_first_response_ms=llm.get("llm_first_response_ms"),
            llm_request_count=llm.get("llm_request_count", 0),
            input_token_count=llm.get("input_token_count", 0),
            output_token_count=llm.get("output_token_count", 0),
            total_token_count=llm.get("total_token_count", 0),
            reasoning_token_count=llm.get("reasoning_token_count", 0),
            estimated_cost_usd=llm.get("estimated_cost_usd"),
            has_error=bool(error_type),
            error_type=error_type,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            started_at=now,
            finished_at=now,
        )

    @staticmethod
    def _build_event_logs(
        *,
        task: ChatTaskLog,
        run: ChatRunLog,
        request: ChatRequest,
        response: ChatResponse,
        trace_entries: list[LoopTraceEntry],
    ) -> list[ChatRunEventLog]:
        event_logs: list[ChatRunEventLog] = [
            ChatRunEventLog(
                event_id=uuid4().hex,
                task_id=task.task_id,
                run_id=run.run_id,
                request_id=run.request_id,
                session_id=task.session_id,
                sequence_no=1,
                event_type="request_received",
                phase="request",
                summary="收到用户请求",
                detail=_safe_preview(request.message or (request.ask_user_answer.answer if request.ask_user_answer else "")),
                payload=_compact_value(
                    {
                        "message": request.message,
                        "has_ask_user_answer": request.ask_user_answer is not None,
                        "mode": request.mode,
                        "client_type": request.client_type,
                    }
                ),
            )
        ]

        sequence_no = 2
        for entry in trace_entries:
            payload = _compact_value(entry.payload)
            tool_name = str(entry.payload.get("tool_name") or "").strip() or None
            event_logs.append(
                ChatRunEventLog(
                    event_id=uuid4().hex,
                    task_id=task.task_id,
                    run_id=run.run_id,
                    request_id=run.request_id,
                    session_id=task.session_id,
                    sequence_no=sequence_no,
                    event_type=entry.event_type,
                    phase=_resolve_phase(entry.event_type),
                    tool_name=tool_name,
                    summary=_resolve_event_summary(entry.event_type, entry.payload, entry.detail),
                    detail=entry.detail,
                    payload=payload,
                )
            )
            sequence_no += 1

        event_logs.append(
            ChatRunEventLog(
                event_id=uuid4().hex,
                task_id=task.task_id,
                run_id=run.run_id,
                request_id=run.request_id,
                session_id=task.session_id,
                sequence_no=sequence_no,
                event_type="response_emitted",
                phase="answer",
                summary="返回最终响应",
                detail=run.response_preview,
                payload=_compact_value({"response_type": response.type, "business": response.business}),
            )
        )
        return event_logs

    @staticmethod
    def _apply_run_to_task(
        *,
        task: ChatTaskLog,
        request: ChatRequest,
        response: ChatResponse,
        run: ChatRunLog,
        tool_names: list[str],
        tool_call_count: int,
        external_tool_call_count: int,
        now: datetime,
    ) -> None:
        if not task.root_question:
            task.root_question = (request.message or "").strip()
        if request.message:
            task.latest_user_message = request.message.strip()

        task.business_type = response.business or task.business_type
        task.first_request_id = task.first_request_id or run.request_id
        task.last_request_id = run.request_id
        task.final_response_type = response.type
        task.final_response_preview = run.response_preview
        task.final_response_payload = run.response_payload
        task.task_status = run.run_status
        task.end_reason = run.end_reason
        task.convergence_mode = run.convergence_mode
        task.latest_ask_user_question = run.ask_user_question
        task.latest_missing_fields = run.missing_fields
        task.ask_user_triggered = bool(task.ask_user_triggered or run.ask_user_count > 0)
        task.ask_user_count = int(task.ask_user_count or 0) + int(run.ask_user_count or 0)
        task.run_count = int(task.run_count or 0) + 1
        task.tool_call_count = int(task.tool_call_count or 0) + tool_call_count
        task.external_tool_call_count = int(task.external_tool_call_count or 0) + external_tool_call_count
        task.total_elapsed_ms = int(task.total_elapsed_ms or 0) + int(run.elapsed_ms or 0)

        combined_tool_names = [str(name) for name in (task.main_tool_names or []) if str(name).strip()]
        combined_tool_names.extend(tool_names)
        ordered = [name for name, _count in Counter(combined_tool_names).most_common(8)]
        task.main_tool_names = ordered

        task.has_error = bool(task.has_error or run.has_error)
        task.error_type = run.error_type or task.error_type
        task.error_message = run.error_message or task.error_message

        if run.run_status == "waiting_user":
            task.finished_at = None
        else:
            task.finished_at = now
