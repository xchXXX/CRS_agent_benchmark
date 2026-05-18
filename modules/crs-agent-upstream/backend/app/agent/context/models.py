"""Structured shared case context models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaseContextArtifactType(str, Enum):
    IMAGE_EVIDENCE = "image_evidence"
    DOC_SEARCH_RESULT = "doc_search_result"
    DIAGNOSIS_RESULT = "diagnosis_result"
    PARAMETER_RESULT = "parameter_result"
    REPAIR_KNOWLEDGE_RESULT = "repair_knowledge_result"
    USER_ANSWER = "user_answer"
    PENDING_ACTION = "pending_action"


class CaseContextSlots(BaseModel):
    brand: str | None = None
    series: str | None = None
    model: str | None = None
    platform: str | None = None
    engine: str | None = None
    emission: str | None = None
    doc_type: str | None = None
    fault_code: str | None = None
    symptom: str | None = None
    subsystem: str | None = None
    ecu_model: str | None = None
    selected_doc_ids: list[str] = Field(default_factory=list)
    selected_doc_titles: list[str] = Field(default_factory=list)
    parameter_source_id: str | None = None


class CaseContextArtifact(BaseModel):
    artifact_id: str
    type: CaseContextArtifactType
    source_business: str
    summary: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    derived_slots: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 1.0
    created_at: str = Field(default_factory=utcnow_iso)
    supersedes: str | None = None


class CaseContextPendingAction(BaseModel):
    scene: str
    tool_call_id: str
    business: str
    question: str
    options_summary: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow_iso)


class CaseContextBudgetState(BaseModel):
    artifact_count: int = 0
    per_type: dict[str, int] = Field(default_factory=dict)
    serialized_bytes: int = 0


class CaseContextAttemptedAction(BaseModel):
    action: str
    args_signature: str
    result_summary: str
    info_gain: str | None = None
    filled_slots: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow_iso)


class CaseContextCandidateAnswer(BaseModel):
    business: str
    summary: str
    source: str
    confidence: float = 1.0
    created_at: str = Field(default_factory=utcnow_iso)


class CaseContextRemainingBudget(BaseModel):
    tool_calls_left: int | None = None
    external_calls_left: int | None = None
    ask_user_calls_left: int | None = None


class CaseContext(BaseModel):
    session_id: str
    revision: int = 0
    updated_at: str = Field(default_factory=utcnow_iso)
    task_type: str | None = None
    slots: CaseContextSlots = Field(default_factory=CaseContextSlots)
    missing_slots: list[str] = Field(default_factory=list)
    artifacts: list[CaseContextArtifact] = Field(default_factory=list)
    attempted_actions: list[CaseContextAttemptedAction] = Field(default_factory=list)
    candidate_answer: CaseContextCandidateAnswer | None = None
    no_gain_streak: int = 0
    answer_ready: bool = False
    remaining_budget: CaseContextRemainingBudget = Field(default_factory=CaseContextRemainingBudget)
    latest_by_type: dict[str, str] = Field(default_factory=dict)
    pending_action: CaseContextPendingAction | None = None
    budgets: CaseContextBudgetState = Field(default_factory=CaseContextBudgetState)
