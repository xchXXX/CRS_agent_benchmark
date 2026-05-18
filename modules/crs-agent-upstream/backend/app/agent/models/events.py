"""Runtime event models."""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.agent.models.ask_user import AskUserQuestion


class AgentEventType(str, Enum):
    START = "start"
    HINT = "hint"
    TEXT_DELTA = "text_delta"
    TOOL_STATUS = "tool_status"
    ASK_USER = "ask_user"
    FALLBACK = "fallback"
    DONE = "done"
    ERROR = "error"


class AgentRuntimeEvent(BaseModel):
    type: AgentEventType
    session_id: Optional[str] = None
    content: Optional[str] = None
    message: Optional[str] = None
    tool_name: Optional[str] = None
    status: Optional[str] = None
    ask_user: Optional[AskUserQuestion] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
