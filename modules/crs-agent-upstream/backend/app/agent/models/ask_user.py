"""AskUser models."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agent.models.tool_result import SelectionPayload


class AskUserInputType(str, Enum):
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    NUMBER = "number"
    TEXT = "text"


class AskUserOption(BaseModel):
    key: str
    label: str
    description: Optional[str] = None
    selection_payload: SelectionPayload = Field(default_factory=SelectionPayload)


class AskUserQuestion(BaseModel):
    tool_call_id: str = Field(..., description="Deferred tool call id")
    question: str
    input_type: AskUserInputType
    options: List[AskUserOption] = Field(default_factory=list)
    allow_free_input: bool = False
    input_hint: Optional[str] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
