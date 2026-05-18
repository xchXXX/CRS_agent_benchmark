"""Runtime mode selection."""

from enum import Enum
from typing import Optional


class ChatRuntimeMode(str, Enum):
    AGENT_LOOP = "agent_loop"
    SHADOW = "shadow"


class RuntimeSelector:
    """Resolve runtime mode from configuration or request override."""

    def __init__(self, default_mode: ChatRuntimeMode = ChatRuntimeMode.AGENT_LOOP):
        self._default_mode = default_mode

    def resolve(self, requested_mode: Optional[str] = None) -> ChatRuntimeMode:
        if not requested_mode:
            return self._default_mode

        normalized = requested_mode.strip().lower()
        try:
            return ChatRuntimeMode(normalized)
        except ValueError:
            return self._default_mode

