"""管理后台仪表盘摘要接口"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agent.observability.task_log_service import AgentTaskLogService
from app.benchmark.doc_search import DocSearchBenchmarkStore
from app.legacy.models.database import ChatTaskLog, DimFacet, DimValue, UserFeedback, get_db
from app.legacy.services.dimension_service import dimension_service
from app.legacy.utils.auth import TokenData, get_current_user


router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])


@router.get("/summary")
async def get_dashboard_summary(
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    now = datetime.now()
    last_7d = now - timedelta(days=7)
    last_30d = now - timedelta(days=30)
    AgentTaskLogService.ensure_tables(db)

    facet_count = db.query(DimFacet).filter(DimFacet.is_active.is_(True)).count()
    value_count = db.query(DimValue).filter(DimValue.is_active.is_(True)).count()

    log_total = db.query(func.count(ChatTaskLog.id)).scalar() or 0
    log_7d = db.query(func.count(ChatTaskLog.id)).filter(ChatTaskLog.created_at >= last_7d).scalar() or 0
    avg_elapsed_7d = (
        db.query(func.avg(ChatTaskLog.total_elapsed_ms))
        .filter(ChatTaskLog.created_at >= last_7d, ChatTaskLog.total_elapsed_ms.isnot(None))
        .scalar()
    )
    latest_log_at = db.query(func.max(ChatTaskLog.created_at)).scalar()

    top_businesses = (
        db.query(ChatTaskLog.business_type, func.count(ChatTaskLog.id).label("count"))
        .filter(ChatTaskLog.business_type.isnot(None))
        .group_by(ChatTaskLog.business_type)
        .order_by(func.count(ChatTaskLog.id).desc())
        .limit(5)
        .all()
    )
    status_distribution = (
        db.query(ChatTaskLog.task_status, func.count(ChatTaskLog.id).label("count"))
        .filter(ChatTaskLog.task_status.isnot(None))
        .group_by(ChatTaskLog.task_status)
        .order_by(func.count(ChatTaskLog.id).desc())
        .limit(5)
        .all()
    )

    feedback_total = db.query(func.count(UserFeedback.id)).scalar() or 0
    feedback_30d = db.query(func.count(UserFeedback.id)).filter(UserFeedback.created_at >= last_30d).scalar() or 0
    avg_rating_30d = db.query(func.avg(UserFeedback.rating)).filter(UserFeedback.created_at >= last_30d).scalar()
    feedback_with_comment_30d = (
        db.query(func.count(UserFeedback.id))
        .filter(
            UserFeedback.created_at >= last_30d,
            UserFeedback.comment.isnot(None),
            UserFeedback.comment != "",
        )
        .scalar()
        or 0
    )
    latest_feedback_at = db.query(func.max(UserFeedback.created_at)).scalar()
    benchmark_store = DocSearchBenchmarkStore()
    benchmark_datasets = benchmark_store.list_datasets()
    benchmark_runs = benchmark_store.list_runs()
    latest_benchmark_run = benchmark_runs[0] if benchmark_runs else None
    latest_benchmark_summary = (latest_benchmark_run or {}).get("summary") or {}

    return {
        "dimensions": {
            "facet_count": facet_count,
            "value_count": value_count,
            "cache_loaded": dimension_service.is_loaded,
        },
        "logs": {
            "total_count": log_total,
            "last_7d_count": log_7d,
            "avg_elapsed_ms_7d": int(avg_elapsed_7d) if avg_elapsed_7d is not None else None,
            "latest_created_at": latest_log_at.isoformat() if latest_log_at else None,
            "top_businesses": [
                {"business_type": business_type, "count": count} for business_type, count in top_businesses
            ],
            "status_distribution": [
                {"task_status": task_status, "count": count}
                for task_status, count in status_distribution
            ],
        },
        "feedback": {
            "total_count": feedback_total,
            "last_30d_count": feedback_30d,
            "avg_rating_30d": round(float(avg_rating_30d), 1) if avg_rating_30d is not None else None,
            "with_comment_30d": feedback_with_comment_30d,
            "latest_created_at": latest_feedback_at.isoformat() if latest_feedback_at else None,
        },
        "benchmarks": {
            "dataset_count": len(benchmark_datasets),
            "total_cases": sum(int(item.get("case_count") or 0) for item in benchmark_datasets),
            "run_count": len(benchmark_runs),
            "running_count": sum(1 for item in benchmark_runs if item.get("status") == "running"),
            "latest_run_at": latest_benchmark_run.get("finished_at") if latest_benchmark_run else None,
            "latest_recall_at_10": latest_benchmark_summary.get("recall_at_10"),
            "latest_track": latest_benchmark_run.get("track") if latest_benchmark_run else None,
        },
    }
