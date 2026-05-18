"""Chat-facing API schemas."""

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.agent.models.ask_user import AskUserQuestion


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class AskUserAnswer(BaseModel):
    """External answer returned for a deferred ask_user_question tool call."""

    tool_call_id: str = Field(..., description="Deferred tool call id")
    answer: Any = Field(..., description="User answer payload")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extra frontend metadata")


class LifecycleCheck(BaseModel):
    """Frontend lifecycle snapshot used for explicit business switching."""

    current_lifecycle: Optional[str] = Field(default=None, description="Frontend current lifecycle state")
    current_business: Optional[str] = Field(default=None, description="Frontend current business type")
    has_ongoing: bool = Field(default=False, description="Whether the frontend has an ongoing interaction")
    user_confirmed_switch: bool = Field(default=False, description="Whether the user confirmed switching topics")


class ChatRequest(BaseModel):
    """Unified chat request for the new project."""

    message: str = Field(default="", description="User input or optional follow-up prompt")
    session_id: Optional[str] = Field(None, description="Session identifier")
    mode: str = Field(default="auto", description="Run mode")
    client_type: str = Field(default="web", description="Client type")
    context: Dict[str, Any] = Field(default_factory=dict, description="Frontend context payload")
    ask_user_answer: Optional[AskUserAnswer] = Field(
        default=None,
        description="Deferred ask_user_question result returned from the frontend.",
    )
    lifecycle_check: Optional[LifecycleCheck] = Field(
        default=None,
        description="Frontend lifecycle state used when the user explicitly switches to a new topic.",
    )

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if not _SESSION_ID_RE.fullmatch(value):
            raise ValueError("session_id must contain only letters, numbers, '_' or '-'")
        return value


class ClarifyOption(BaseModel):
    """Frontend option item."""

    key: str
    label: str
    description: Optional[str] = None
    selection_payload: Dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Unified chat response."""

    type: str
    content: Any
    session_id: str
    request_id: Optional[str] = None
    business: Optional[str] = None
    need_clarify: bool = False
    clarify_options: List[ClarifyOption] = Field(default_factory=list)
    clarify_facet: Optional[str] = None
    lifecycle_info: Optional[Dict[str, Any]] = None
    result_summary: Optional[Dict[str, Any]] = None
    hints: List[Dict[str, Any]] = Field(default_factory=list)
    suggestions: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ask_user: Optional[AskUserQuestion] = None


class StreamAbortRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier")
    partial_content: str = Field(default="", description="Partially streamed content")

    @field_validator("session_id")
    @classmethod
    def validate_abort_session_id(cls, value: str) -> str:
        if not _SESSION_ID_RE.fullmatch(value):
            raise ValueError("session_id must contain only letters, numbers, '_' or '-'")
        return value
