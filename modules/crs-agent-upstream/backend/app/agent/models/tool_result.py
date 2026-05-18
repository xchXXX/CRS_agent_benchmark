"""Tool output models."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ToolResultStatus(str, Enum):
    OK = "ok"
    NEED_CLARIFY = "need_clarify"
    DEFERRED = "deferred"
    FAILED = "failed"


class SelectionPayload(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict)
    file_ids: List[str] = Field(default_factory=list)


class ClarifyCandidateOption(BaseModel):
    key: str
    label: str
    description: Optional[str] = None
    selection_payload: SelectionPayload = Field(default_factory=SelectionPayload)


class ClarifyCandidate(BaseModel):
    source: str
    question: str
    results_count: int
    options: List[ClarifyCandidateOption] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class ToolResultEnvelope(BaseModel):
    status: ToolResultStatus
    data: Dict[str, Any] = Field(default_factory=dict)
    clarify: Optional[ClarifyCandidate] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
