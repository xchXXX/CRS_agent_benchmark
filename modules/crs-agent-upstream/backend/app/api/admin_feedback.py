"""管理端反馈管理接口"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import collate

from app.agent.observability.task_log_service import AgentTaskLogService
from app.api.admin_logs import _serialize_task_detail, _serialize_task_item
from app.legacy.models.database import (
    ChatLog,
    ChatRunEventLog,
    ChatRunLog,
    ChatTaskLog,
    UserFeedback,
    get_db,
)
from app.legacy.utils.auth import TokenData, get_current_user


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/feedback", tags=["admin-feedback"])


class FeedbackListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list


def _task_logs_available(db: Session) -> bool:
    try:
        AgentTaskLogService.ensure_tables(db)
        return True
    except Exception as exc:
        logger.warning("Feedback API cannot use task logs, fallback to legacy chat logs: %s", exc)
        return False


def _request_id_join(db: Session, left, right):
    dialect_name = getattr(getattr(db, "bind", None), "dialect", None)
    if getattr(dialect_name, "name", None) == "mysql":
        return collate(left, "utf8mb4_unicode_ci") == collate(right, "utf8mb4_unicode_ci")
    return left == right


def _serialize_legacy_chat_log_summary(chat_log: ChatLog | None) -> dict | None:
    if chat_log is None:
        return None
    user_message = chat_log.user_message
    return {
        "user_message": (user_message[:100] + "...") if user_message and len(user_message) > 100 else user_message,
        "response_type": chat_log.response_type,
        "response_preview": chat_log.response_preview,
        "elapsed_ms": chat_log.elapsed_ms,
    }


def _serialize_legacy_chat_log_detail(chat_log: ChatLog | None) -> dict | None:
    if chat_log is None:
        return None
    return {
        "id": chat_log.id,
        "request_id": chat_log.request_id,
        "session_id": chat_log.session_id,
        "user_message": chat_log.user_message,
        "client_type": chat_log.client_type,
        "request_mode": chat_log.request_mode,
        "intent_type": chat_log.intent_type,
        "intent_confidence": float(chat_log.intent_confidence) if chat_log.intent_confidence else None,
        "response_type": chat_log.response_type,
        "response_content": chat_log.response_content,
        "response_preview": chat_log.response_preview,
        "elapsed_ms": chat_log.elapsed_ms,
        "report_url": chat_log.report_url,
        "created_at": chat_log.created_at.isoformat() if chat_log.created_at else None,
    }


def _serialize_run_summary(run: ChatRunLog | None) -> dict | None:
    if run is None:
        return None
    return {
        "run_id": run.run_id,
        "request_id": run.request_id,
        "sequence_no": run.sequence_no,
        "trigger_type": run.trigger_type,
        "run_status": run.run_status,
        "end_reason": run.end_reason,
        "response_type": run.response_type,
        "response_preview": run.response_preview,
        "ask_user_question": run.ask_user_question,
        "missing_fields": run.missing_fields or [],
        "tool_call_count": int(run.tool_call_count or 0),
        "external_tool_call_count": int(run.external_tool_call_count or 0),
        "model_provider": run.model_provider,
        "model_name": run.model_name,
        "llm_call_count": int(run.llm_call_count or 0),
        "llm_elapsed_ms": run.llm_elapsed_ms,
        "input_token_count": int(run.input_token_count or 0),
        "output_token_count": int(run.output_token_count or 0),
        "total_token_count": int(run.total_token_count or 0),
        "estimated_cost_usd": float(run.estimated_cost_usd) if run.estimated_cost_usd is not None else None,
        "elapsed_ms": run.elapsed_ms,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _load_task_detail(db: Session, task_id: str | None) -> dict | None:
    if not task_id:
        return None

    task = db.query(ChatTaskLog).filter(ChatTaskLog.task_id == task_id).first()
    if task is None:
        return None

    runs = (
        db.query(ChatRunLog)
        .filter(ChatRunLog.task_id == task.task_id)
        .order_by(ChatRunLog.sequence_no.asc(), ChatRunLog.id.asc())
        .all()
    )

    run_ids = [run.run_id for run in runs if run.run_id]
    events_by_run: dict[str, list[ChatRunEventLog]] = defaultdict(list)
    if run_ids:
        events = (
            db.query(ChatRunEventLog)
            .filter(ChatRunEventLog.run_id.in_(run_ids))
            .order_by(ChatRunEventLog.run_id.asc(), ChatRunEventLog.sequence_no.asc(), ChatRunEventLog.id.asc())
            .all()
        )
        for event in events:
            events_by_run[event.run_id].append(event)

    return _serialize_task_detail(task, runs, events_by_run).model_dump(mode="json")


@router.get("/list", response_model=FeedbackListResponse)
async def get_feedback_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    business_type: Optional[str] = Query(None, description="业务类型筛选"),
    rating_min: Optional[int] = Query(None, ge=1, le=10, description="最低评分"),
    rating_max: Optional[int] = Query(None, ge=1, le=10, description="最高评分"),
    start_time: Optional[str] = Query(None, description="开始时间(ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间(ISO格式)"),
    has_comment: Optional[bool] = Query(None, description="仅含文本评论"),
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    task_logs_available = _task_logs_available(db)

    def build_task_query():
        return (
            db.query(
                UserFeedback,
                ChatRunLog,
                ChatTaskLog,
                ChatLog,
            )
            .outerjoin(ChatRunLog, _request_id_join(db, UserFeedback.request_id, ChatRunLog.request_id))
            .outerjoin(ChatTaskLog, _request_id_join(db, ChatRunLog.task_id, ChatTaskLog.task_id))
            .outerjoin(ChatLog, _request_id_join(db, UserFeedback.request_id, ChatLog.request_id))
        )

    def build_legacy_query():
        return db.query(UserFeedback, ChatLog).outerjoin(ChatLog, _request_id_join(db, UserFeedback.request_id, ChatLog.request_id))

    query = build_task_query() if task_logs_available else build_legacy_query()

    if business_type:
        query = query.filter(UserFeedback.business_type == business_type)
    if rating_min is not None:
        query = query.filter(UserFeedback.rating >= rating_min)
    if rating_max is not None:
        query = query.filter(UserFeedback.rating <= rating_max)
    if start_time:
        try:
            query = query.filter(UserFeedback.created_at >= datetime.fromisoformat(start_time))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="开始时间格式错误") from exc
    if end_time:
        try:
            query = query.filter(UserFeedback.created_at <= datetime.fromisoformat(end_time))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="结束时间格式错误") from exc
    if has_comment:
        query = query.filter(UserFeedback.comment.isnot(None), UserFeedback.comment != "")

    count_query = db.query(func.count(UserFeedback.id))
    if business_type:
        count_query = count_query.filter(UserFeedback.business_type == business_type)
    if rating_min is not None:
        count_query = count_query.filter(UserFeedback.rating >= rating_min)
    if rating_max is not None:
        count_query = count_query.filter(UserFeedback.rating <= rating_max)
    if start_time:
        try:
            count_query = count_query.filter(UserFeedback.created_at >= datetime.fromisoformat(start_time))
        except ValueError:
            pass
    if end_time:
        try:
            count_query = count_query.filter(UserFeedback.created_at <= datetime.fromisoformat(end_time))
        except ValueError:
            pass
    if has_comment:
        count_query = count_query.filter(UserFeedback.comment.isnot(None), UserFeedback.comment != "")

    total = count_query.scalar()
    try:
        rows = query.order_by(desc(UserFeedback.created_at)).offset((page - 1) * page_size).limit(page_size).all()
    except OperationalError as exc:
        if not task_logs_available:
            raise
        logger.warning("Feedback list query failed with task logs; fallback to legacy-only query: %s", exc)
        task_logs_available = False
        rows = (
            build_legacy_query()
            .order_by(desc(UserFeedback.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

    items = []
    for row in rows:
        if task_logs_available:
            feedback, run_log, task_log, chat_log = row
        else:
            feedback, chat_log = row
            run_log = None
            task_log = None
        items.append(
            {
                "id": feedback.id,
                "request_id": feedback.request_id,
                "session_id": feedback.session_id,
                "rating": feedback.rating,
                "business_type": feedback.business_type,
                "tags": feedback.tags,
                "comment": feedback.comment,
                "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                "task_log": _serialize_task_item(task_log) if task_log else None,
                "run_log": _serialize_run_summary(run_log),
                "chat_log": _serialize_legacy_chat_log_summary(chat_log),
            }
        )

    return FeedbackListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/stats")
async def get_feedback_stats(
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    start_date = datetime.now() - timedelta(days=days)
    base_filter = UserFeedback.created_at >= start_date

    total_count = db.query(func.count(UserFeedback.id)).filter(base_filter).scalar() or 0
    avg_rating = db.query(func.avg(UserFeedback.rating)).filter(base_filter).scalar()

    rating_rows = (
        db.query(UserFeedback.rating, func.count(UserFeedback.id).label("count"))
        .filter(base_filter)
        .group_by(UserFeedback.rating)
        .all()
    )
    rating_distribution = [{"rating": rating, "count": count} for rating, count in rating_rows]

    business_rows = (
        db.query(
            UserFeedback.business_type,
            func.count(UserFeedback.id).label("count"),
            func.avg(UserFeedback.rating).label("avg_rating"),
        )
        .filter(base_filter)
        .group_by(UserFeedback.business_type)
        .all()
    )
    business_stats = [
        {
            "business_type": business_type,
            "count": count,
            "avg_rating": round(float(avg_rating), 1) if avg_rating else None,
        }
        for business_type, count, avg_rating in business_rows
    ]

    tag_rows = db.query(UserFeedback.tags).filter(base_filter, UserFeedback.tags.isnot(None)).all()
    tag_counter: dict[str, int] = {}
    for (tags,) in tag_rows:
        if isinstance(tags, list):
            for tag in tags:
                tag_counter[tag] = tag_counter.get(tag, 0) + 1

    top_tags = sorted(
        [{"tag": tag, "count": count} for tag, count in tag_counter.items()],
        key=lambda item: item["count"],
        reverse=True,
    )[:20]

    return {
        "total_count": total_count,
        "avg_rating": round(float(avg_rating), 1) if avg_rating else None,
        "rating_distribution": rating_distribution,
        "business_stats": business_stats,
        "top_tags": top_tags,
    }


@router.get("/{feedback_id}")
async def get_feedback_detail(
    feedback_id: int,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    task_logs_available = _task_logs_available(db)

    feedback = db.query(UserFeedback).filter(UserFeedback.id == feedback_id).first()
    if not feedback:
        raise HTTPException(status_code=404, detail="反馈记录不存在")

    run_log = None
    task_log_data = None
    if task_logs_available:
        try:
            run_log = db.query(ChatRunLog).filter(ChatRunLog.request_id == feedback.request_id).first()
            task_log_data = _load_task_detail(db, run_log.task_id if run_log else None)
        except Exception as exc:
            logger.warning("Feedback detail fallback to legacy chat log for request %s: %s", feedback.request_id, exc)
            run_log = None
            task_log_data = None

    chat_log = db.query(ChatLog).filter(ChatLog.request_id == feedback.request_id).first()
    chat_log_data = _serialize_legacy_chat_log_detail(chat_log)

    return {
        "id": feedback.id,
        "request_id": feedback.request_id,
        "session_id": feedback.session_id,
        "rating": feedback.rating,
        "business_type": feedback.business_type,
        "tags": feedback.tags,
        "comment": feedback.comment,
        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
        "task_log": task_log_data,
        "run_log": _serialize_run_summary(run_log),
        "chat_log": chat_log_data,
    }
