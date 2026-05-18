"""Tool metadata."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ToolExecutionMode(str, Enum):
    INLINE = "inline"
    DEFERRED = "deferred"


class ToolSpec(BaseModel):
    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    execution_mode: ToolExecutionMode = ToolExecutionMode.INLINE
    owner: str = "agent"
    tags: List[str] = Field(default_factory=list)
    deprecated: bool = False
    replacement: Optional[str] = None

