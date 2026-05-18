"""用户反馈接口。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.legacy.models.database import UserFeedback, get_db


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])

VALID_BUSINESS_TYPES = {"DOC_SEARCH", "FAULT_DIAGNOSIS", "GENERAL_CHAT", "PARAM_QUERY", "AGENT_LOOP"}


class FeedbackRequest(BaseModel):
    request_id: str = Field(..., max_length=36, description="关联请求ID")
    session_id: str = Field(..., max_length=64, description="会话ID")
    rating: int = Field(..., ge=1, le=10, description="评分1-10")
    business_type: str = Field(..., max_length=30, description="业务类型")
    tags: Optional[list[str]] = Field(default=None, description="快捷标签")
    comment: Optional[str] = Field(default=None, max_length=500, description="文本反馈")


class FeedbackResponse(BaseModel):
    success: bool
    id: int


@router.post("", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    db: Session = Depends(get_db),
):
    if req.business_type not in VALID_BUSINESS_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"无效的业务类型，允许值: {', '.join(sorted(VALID_BUSINESS_TYPES))}",
        )

    existing = db.query(UserFeedback).filter(UserFeedback.request_id == req.request_id).first()
    if existing is not None:
        return FeedbackResponse(success=True, id=int(existing.id))

    feedback = UserFeedback(
        request_id=req.request_id,
        session_id=req.session_id,
        rating=req.rating,
        business_type=req.business_type,
        tags=req.tags,
        comment=req.comment.strip() if req.comment else None,
    )

    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    logger.info("反馈已提交: id=%s request_id=%s rating=%s", feedback.id, req.request_id, req.rating)
    return FeedbackResponse(success=True, id=int(feedback.id))
