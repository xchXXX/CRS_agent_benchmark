"""Loop-oriented admin log APIs."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.agent.observability.task_log_service import AgentTaskLogService
from app.legacy.models.database import ChatRunEventLog, ChatRunLog, ChatTaskLog, get_db
from app.legacy.utils.auth import TokenData, get_current_user, require_admin


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/logs", tags=["admin-logs"])


class LogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]]


class RunEventResponse(BaseModel):
    id: int
    event_id: str
    sequence_no: int
    event_type: str
    phase: Optional[str]
    tool_name: Optional[str]
    summary: Optional[str]
    detail: Optional[str]
    payload: Optional[dict[str, Any]]
    created_at: Optional[str]


class RunDetailResponse(BaseModel):
    id: int
    run_id: str
    request_id: str
    sequence_no: int
    trigger_type: Optional[str]
    transport: Optional[str]
    request_mode: Optional[str]
    input_message: Optional[str]
    ask_user_answer_summary: Optional[str]
    business_type: Optional[str]
    run_status: str
    end_reason: Optional[str]
    convergence_mode: Optional[str]
    guard_error_code: Optional[str]
    response_type: Optional[str]
    response_preview: Optional[str]
    response_payload: Optional[dict[str, Any]]
    response_metadata: Optional[dict[str, Any]]
    ask_user_question: Optional[str]
    missing_fields: list[str]
    ask_user_count: int
    tool_call_count: int
    external_tool_call_count: int
    tool_names: list[str]
    model_provider: Optional[str]
    model_name: Optional[str]
    llm_call_count: int
    llm_elapsed_ms: Optional[int]
    llm_first_response_ms: Optional[int]
    llm_request_count: int
    input_token_count: int
    output_token_count: int
    total_token_count: int
    reasoning_token_count: int
    estimated_cost_usd: Optional[float]
    has_error: bool
    error_type: Optional[str]
    error_message: Optional[str]
    elapsed_ms: Optional[int]
    started_at: Optional[str]
    finished_at: Optional[str]
    events: list[RunEventResponse]


class LogDetailResponse(BaseModel):
    id: int
    task_id: str
    session_id: str
    user_id: Optional[str]
    client_type: Optional[str]
    root_question: str
    latest_user_message: Optional[str]
    business_type: Optional[str]
    task_status: str
    end_reason: Optional[str]
    convergence_mode: Optional[str]
    final_response_type: Optional[str]
    final_response_preview: Optional[str]
    final_response_payload: Optional[dict[str, Any]]
    latest_ask_user_question: Optional[str]
    latest_missing_fields: list[str]
    ask_user_triggered: bool
    ask_user_count: int
    run_count: int
    tool_call_count: int
    external_tool_call_count: int
    main_tool_names: list[str]
    has_error: bool
    error_type: Optional[str]
    error_message: Optional[str]
    first_request_id: Optional[str]
    last_request_id: Optional[str]
    replaces_task_id: Optional[str]
    replaced_by_task_id: Optional[str]
    total_elapsed_ms: Optional[int]
    started_at: Optional[str]
    finished_at: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    runs: list[RunDetailResponse]


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name}格式错误") from exc


def _parse_user_id(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if not normalized.isdigit():
        raise HTTPException(status_code=400, detail="用户ID格式错误")
    return int(normalized)


def _serialize_task_item(task: ChatTaskLog) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_id": task.task_id,
        "session_id": task.session_id,
        "user_id": str(task.user_id) if task.user_id is not None else None,
        "client_type": task.client_type,
        "root_question": task.root_question,
        "latest_user_message": task.latest_user_message,
        "business_type": task.business_type,
        "task_status": task.task_status,
        "end_reason": task.end_reason,
        "convergence_mode": task.convergence_mode,
        "final_response_type": task.final_response_type,
        "final_response_preview": task.final_response_preview,
        "latest_ask_user_question": task.latest_ask_user_question,
        "latest_missing_fields": task.latest_missing_fields or [],
        "ask_user_triggered": bool(task.ask_user_triggered),
        "ask_user_count": int(task.ask_user_count or 0),
        "run_count": int(task.run_count or 0),
        "tool_call_count": int(task.tool_call_count or 0),
        "external_tool_call_count": int(task.external_tool_call_count or 0),
        "main_tool_names": task.main_tool_names or [],
        "has_error": bool(task.has_error),
        "error_type": task.error_type,
        "total_elapsed_ms": task.total_elapsed_ms,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


def _serialize_event(event: ChatRunEventLog) -> RunEventResponse:
    return RunEventResponse(
        id=event.id,
        event_id=event.event_id,
        sequence_no=event.sequence_no,
        event_type=event.event_type,
        phase=event.phase,
        tool_name=event.tool_name,
        summary=event.summary,
        detail=event.detail,
        payload=event.payload or {},
        created_at=event.created_at.isoformat() if event.created_at else None,
    )


def _serialize_run(run: ChatRunLog, events: list[ChatRunEventLog]) -> RunDetailResponse:
    return RunDetailResponse(
        id=run.id,
        run_id=run.run_id,
        request_id=run.request_id,
        sequence_no=run.sequence_no,
        trigger_type=run.trigger_type,
        transport=run.transport,
        request_mode=run.request_mode,
        input_message=run.input_message,
        ask_user_answer_summary=run.ask_user_answer_summary,
        business_type=run.business_type,
        run_status=run.run_status,
        end_reason=run.end_reason,
        convergence_mode=run.convergence_mode,
        guard_error_code=run.guard_error_code,
        response_type=run.response_type,
        response_preview=run.response_preview,
        response_payload=run.response_payload or {},
        response_metadata=run.response_metadata or {},
        ask_user_question=run.ask_user_question,
        missing_fields=run.missing_fields or [],
        ask_user_count=int(run.ask_user_count or 0),
        tool_call_count=int(run.tool_call_count or 0),
        external_tool_call_count=int(run.external_tool_call_count or 0),
        tool_names=run.tool_names or [],
        model_provider=run.model_provider,
        model_name=run.model_name,
        llm_call_count=int(run.llm_call_count or 0),
        llm_elapsed_ms=run.llm_elapsed_ms,
        llm_first_response_ms=run.llm_first_response_ms,
        llm_request_count=int(run.llm_request_count or 0),
        input_token_count=int(run.input_token_count or 0),
        output_token_count=int(run.output_token_count or 0),
        total_token_count=int(run.total_token_count or 0),
        reasoning_token_count=int(run.reasoning_token_count or 0),
        estimated_cost_usd=float(run.estimated_cost_usd) if run.estimated_cost_usd is not None else None,
        has_error=bool(run.has_error),
        error_type=run.error_type,
        error_message=run.error_message,
        elapsed_ms=run.elapsed_ms,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        events=[_serialize_event(event) for event in events],
    )


def _serialize_task_detail(task: ChatTaskLog, runs: list[ChatRunLog], events_by_run: dict[str, list[ChatRunEventLog]]) -> LogDetailResponse:
    return LogDetailResponse(
        id=task.id,
        task_id=task.task_id,
        session_id=task.session_id,
        user_id=str(task.user_id) if task.user_id is not None else None,
        client_type=task.client_type,
        root_question=task.root_question,
        latest_user_message=task.latest_user_message,
        business_type=task.business_type,
        task_status=task.task_status,
        end_reason=task.end_reason,
        convergence_mode=task.convergence_mode,
        final_response_type=task.final_response_type,
        final_response_preview=task.final_response_preview,
        final_response_payload=task.final_response_payload or {},
        latest_ask_user_question=task.latest_ask_user_question,
        latest_missing_fields=task.latest_missing_fields or [],
        ask_user_triggered=bool(task.ask_user_triggered),
        ask_user_count=int(task.ask_user_count or 0),
        run_count=int(task.run_count or 0),
        tool_call_count=int(task.tool_call_count or 0),
        external_tool_call_count=int(task.external_tool_call_count or 0),
        main_tool_names=task.main_tool_names or [],
        has_error=bool(task.has_error),
        error_type=task.error_type,
        error_message=task.error_message,
        first_request_id=task.first_request_id,
        last_request_id=task.last_request_id,
        replaces_task_id=task.replaces_task_id,
        replaced_by_task_id=task.replaced_by_task_id,
        total_elapsed_ms=task.total_elapsed_ms,
        started_at=task.started_at.isoformat() if task.started_at else None,
        finished_at=task.finished_at.isoformat() if task.finished_at else None,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
        runs=[_serialize_run(run, events_by_run.get(run.run_id, [])) for run in runs],
    )


def _apply_task_filters(
    query,
    *,
    db: Session,
    keyword: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    business_type: Optional[str] = None,
    task_status: Optional[str] = None,
    end_reason: Optional[str] = None,
    convergence_mode: Optional[str] = None,
    ask_user_triggered: Optional[bool] = None,
    has_error: Optional[bool] = None,
    uses_external_tools: Optional[bool] = None,
    tool_name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    min_tool_calls: Optional[int] = None,
    max_tool_calls: Optional[int] = None,
    min_elapsed_ms: Optional[int] = None,
    max_elapsed_ms: Optional[int] = None,
):
    if keyword:
        query = query.filter(
            ChatTaskLog.root_question.contains(keyword)
            | ChatTaskLog.latest_user_message.contains(keyword)
            | ChatTaskLog.final_response_preview.contains(keyword)
        )

    parsed_user_id = _parse_user_id(user_id)
    if parsed_user_id is not None:
        query = query.filter(ChatTaskLog.user_id == parsed_user_id)

    if session_id:
        query = query.filter(ChatTaskLog.session_id == session_id.strip())
    if task_id:
        query = query.filter(ChatTaskLog.task_id == task_id.strip())
    if business_type:
        query = query.filter(ChatTaskLog.business_type == business_type)
    if task_status:
        query = query.filter(ChatTaskLog.task_status == task_status)
    if end_reason:
        query = query.filter(ChatTaskLog.end_reason == end_reason)
    if convergence_mode:
        query = query.filter(ChatTaskLog.convergence_mode == convergence_mode)
    if ask_user_triggered is not None:
        query = query.filter(ChatTaskLog.ask_user_triggered.is_(ask_user_triggered))
    if has_error is not None:
        query = query.filter(ChatTaskLog.has_error.is_(has_error))
    if uses_external_tools is not None:
        if uses_external_tools:
            query = query.filter(ChatTaskLog.external_tool_call_count > 0)
        else:
            query = query.filter(func.coalesce(ChatTaskLog.external_tool_call_count, 0) == 0)
    if start_time:
        query = query.filter(ChatTaskLog.created_at >= _parse_datetime(start_time, "开始时间"))
    if end_time:
        query = query.filter(ChatTaskLog.created_at <= _parse_datetime(end_time, "结束时间"))
    if min_tool_calls is not None:
        query = query.filter(func.coalesce(ChatTaskLog.tool_call_count, 0) >= min_tool_calls)
    if max_tool_calls is not None:
        query = query.filter(func.coalesce(ChatTaskLog.tool_call_count, 0) <= max_tool_calls)
    if min_elapsed_ms is not None:
        query = query.filter(func.coalesce(ChatTaskLog.total_elapsed_ms, 0) >= min_elapsed_ms)
    if max_elapsed_ms is not None:
        query = query.filter(func.coalesce(ChatTaskLog.total_elapsed_ms, 0) <= max_elapsed_ms)
    if tool_name:
        tool_name = tool_name.strip()
        if tool_name:
            query = query.filter(
                db.query(ChatRunEventLog.id)
                .filter(ChatRunEventLog.task_id == ChatTaskLog.task_id, ChatRunEventLog.tool_name == tool_name)
                .exists()
            )
    return query


@router.get("/list", response_model=LogListResponse)
async def get_log_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    user_id: Optional[str] = Query(None, description="用户ID筛选"),
    session_id: Optional[str] = Query(None, description="会话ID筛选"),
    task_id: Optional[str] = Query(None, description="任务ID筛选"),
    business_type: Optional[str] = Query(None, description="业务类型筛选"),
    task_status: Optional[str] = Query(None, description="任务状态筛选"),
    end_reason: Optional[str] = Query(None, description="结束原因筛选"),
    convergence_mode: Optional[str] = Query(None, description="收敛模式筛选"),
    ask_user_triggered: Optional[bool] = Query(None, description="是否触发 ask_user"),
    has_error: Optional[bool] = Query(None, description="是否报错"),
    uses_external_tools: Optional[bool] = Query(None, description="是否使用外部工具"),
    tool_name: Optional[str] = Query(None, description="工具名称筛选"),
    min_tool_calls: Optional[int] = Query(None, ge=0, description="最少工具调用次数"),
    max_tool_calls: Optional[int] = Query(None, ge=0, description="最多工具调用次数"),
    min_elapsed_ms: Optional[int] = Query(None, ge=0, description="最短耗时"),
    max_elapsed_ms: Optional[int] = Query(None, ge=0, description="最长耗时"),
    start_time: Optional[str] = Query(None, description="开始时间(ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间(ISO格式)"),
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    AgentTaskLogService.ensure_tables(db)

    query = _apply_task_filters(
        db.query(ChatTaskLog),
        db=db,
        keyword=keyword,
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
        business_type=business_type,
        task_status=task_status,
        end_reason=end_reason,
        convergence_mode=convergence_mode,
        ask_user_triggered=ask_user_triggered,
        has_error=has_error,
        uses_external_tools=uses_external_tools,
        tool_name=tool_name,
        start_time=start_time,
        end_time=end_time,
        min_tool_calls=min_tool_calls,
        max_tool_calls=max_tool_calls,
        min_elapsed_ms=min_elapsed_ms,
        max_elapsed_ms=max_elapsed_ms,
    )

    total = query.count()
    items = (
        query.order_by(desc(ChatTaskLog.created_at), desc(ChatTaskLog.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return LogListResponse(total=total, page=page, page_size=page_size, items=[_serialize_task_item(item) for item in items])


@router.get("/stats/summary")
async def get_log_stats(
    days: int = Query(7, ge=1, le=90, description="统计天数"),
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    AgentTaskLogService.ensure_tables(db)

    start_date = datetime.now() - timedelta(days=days)
    base_query = db.query(ChatTaskLog).filter(ChatTaskLog.created_at >= start_date)
    total = base_query.count()
    completed_count = base_query.filter(ChatTaskLog.task_status == "completed").count()
    waiting_user_count = base_query.filter(ChatTaskLog.task_status == "waiting_user").count()
    guard_stopped_count = base_query.filter(ChatTaskLog.task_status == "guard_stopped").count()
    failed_count = base_query.filter(ChatTaskLog.task_status == "failed").count()
    switched_count = base_query.filter(ChatTaskLog.task_status == "switched").count()
    ask_user_count = base_query.filter(ChatTaskLog.ask_user_triggered.is_(True)).count()

    avg_elapsed = base_query.with_entities(func.avg(ChatTaskLog.total_elapsed_ms)).scalar()
    avg_tool_calls = base_query.with_entities(func.avg(ChatTaskLog.tool_call_count)).scalar()
    avg_external_tool_calls = base_query.with_entities(func.avg(ChatTaskLog.external_tool_call_count)).scalar()
    latest_log_at = base_query.with_entities(func.max(ChatTaskLog.created_at)).scalar()

    top_businesses = (
        db.query(ChatTaskLog.business_type, func.count(ChatTaskLog.id).label("count"))
        .filter(ChatTaskLog.created_at >= start_date, ChatTaskLog.business_type.isnot(None))
        .group_by(ChatTaskLog.business_type)
        .order_by(func.count(ChatTaskLog.id).desc())
        .limit(8)
        .all()
    )

    return {
        "total": total,
        "completed_count": completed_count,
        "waiting_user_count": waiting_user_count,
        "guard_stopped_count": guard_stopped_count,
        "failed_count": failed_count,
        "switched_count": switched_count,
        "ask_user_rate": round(ask_user_count / total, 4) if total else 0,
        "guard_stop_rate": round(guard_stopped_count / total, 4) if total else 0,
        "avg_elapsed_ms": int(avg_elapsed) if avg_elapsed is not None else None,
        "avg_tool_calls": round(float(avg_tool_calls), 2) if avg_tool_calls is not None else None,
        "avg_external_tool_calls": round(float(avg_external_tool_calls), 2) if avg_external_tool_calls is not None else None,
        "latest_created_at": latest_log_at.isoformat() if latest_log_at else None,
        "top_businesses": [{"business_type": business_type, "count": count} for business_type, count in top_businesses],
    }


@router.get("/export")
async def export_logs(
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    user_id: Optional[str] = Query(None, description="用户ID筛选"),
    session_id: Optional[str] = Query(None, description="会话ID筛选"),
    task_id: Optional[str] = Query(None, description="任务ID筛选"),
    business_type: Optional[str] = Query(None, description="业务类型筛选"),
    task_status: Optional[str] = Query(None, description="任务状态筛选"),
    end_reason: Optional[str] = Query(None, description="结束原因筛选"),
    convergence_mode: Optional[str] = Query(None, description="收敛模式筛选"),
    ask_user_triggered: Optional[bool] = Query(None, description="是否触发 ask_user"),
    has_error: Optional[bool] = Query(None, description="是否报错"),
    uses_external_tools: Optional[bool] = Query(None, description="是否使用外部工具"),
    tool_name: Optional[str] = Query(None, description="工具名称筛选"),
    min_tool_calls: Optional[int] = Query(None, ge=0, description="最少工具调用次数"),
    max_tool_calls: Optional[int] = Query(None, ge=0, description="最多工具调用次数"),
    min_elapsed_ms: Optional[int] = Query(None, ge=0, description="最短耗时"),
    max_elapsed_ms: Optional[int] = Query(None, ge=0, description="最长耗时"),
    start_time: Optional[str] = Query(None, description="开始时间(ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间(ISO格式)"),
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user
    AgentTaskLogService.ensure_tables(db)

    try:
        query = _apply_task_filters(
            db.query(ChatTaskLog),
            db=db,
            keyword=keyword,
            user_id=user_id,
            session_id=session_id,
            task_id=task_id,
            business_type=business_type,
            task_status=task_status,
            end_reason=end_reason,
            convergence_mode=convergence_mode,
            ask_user_triggered=ask_user_triggered,
            has_error=has_error,
            uses_external_tools=uses_external_tools,
            tool_name=tool_name,
            start_time=start_time,
            end_time=end_time,
            min_tool_calls=min_tool_calls,
            max_tool_calls=max_tool_calls,
            min_elapsed_ms=min_elapsed_ms,
            max_elapsed_ms=max_elapsed_ms,
        )
        tasks = query.order_by(desc(ChatTaskLog.created_at), desc(ChatTaskLog.id)).all()

        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(
            [
                "日志ID",
                "任务ID",
                "会话ID",
                "用户ID",
                "首个问题",
                "业务场景",
                "任务状态",
                "结束原因",
                "收敛模式",
                "最终响应类型",
                "最终响应摘要",
                "最近反问",
                "缺失字段",
                "运行次数",
                "ask_user次数",
                "工具调用总数",
                "外部工具总数",
                "总耗时(ms)",
                "错误类型",
                "开始时间",
                "结束时间",
            ]
        )

        for task in tasks:
            writer.writerow(
                [
                    task.id,
                    task.task_id,
                    task.session_id,
                    str(task.user_id) if task.user_id is not None else "",
                    (task.root_question or "").replace("\n", " ").replace("\r", ""),
                    task.business_type or "",
                    task.task_status,
                    task.end_reason or "",
                    task.convergence_mode or "",
                    task.final_response_type or "",
                    (task.final_response_preview or "").replace("\n", " ").replace("\r", ""),
                    (task.latest_ask_user_question or "").replace("\n", " ").replace("\r", ""),
                    " | ".join(task.latest_missing_fields or []),
                    task.run_count or 0,
                    task.ask_user_count or 0,
                    task.tool_call_count or 0,
                    task.external_tool_call_count or 0,
                    task.total_elapsed_ms or 0,
                    task.error_type or "",
                    task.started_at.strftime("%Y-%m-%d %H:%M:%S") if task.started_at else "",
                    task.finished_at.strftime("%Y-%m-%d %H:%M:%S") if task.finished_at else "",
                ]
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_task_logs_{timestamp}.csv"
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("导出日志失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"导出失败: {str(exc)}") from exc


@router.get("/{log_id}", response_model=LogDetailResponse)
async def get_log_detail(
    log_id: int,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    AgentTaskLogService.ensure_tables(db)

    task = db.query(ChatTaskLog).filter(ChatTaskLog.id == log_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="日志不存在")

    runs = (
        db.query(ChatRunLog)
        .filter(ChatRunLog.task_id == task.task_id)
        .order_by(ChatRunLog.sequence_no.asc(), ChatRunLog.created_at.asc(), ChatRunLog.id.asc())
        .all()
    )
    run_ids = [run.run_id for run in runs]
    events = (
        db.query(ChatRunEventLog)
        .filter(ChatRunEventLog.run_id.in_(run_ids))
        .order_by(ChatRunEventLog.sequence_no.asc(), ChatRunEventLog.created_at.asc(), ChatRunEventLog.id.asc())
        .all()
        if run_ids
        else []
    )
    events_by_run: dict[str, list[ChatRunEventLog]] = {}
    for event in events:
        events_by_run.setdefault(event.run_id, []).append(event)

    return _serialize_task_detail(task, runs, events_by_run)
