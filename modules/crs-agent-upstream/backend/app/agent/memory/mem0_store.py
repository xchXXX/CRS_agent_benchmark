"""Mem0 adapter placeholder."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryFact(BaseModel):
    memory_id: Optional[str] = None
    content: str
    score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Mem0Store:
    """Mem0 integration point.

    Current skeleton keeps the interface stable but does not couple to an SDK yet.
    """

    def __init__(self, enabled: bool = False):
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def search(self, user_id: str, query: str, limit: int = 5) -> List[MemoryFact]:
        if not self._enabled:
            return []
        raise NotImplementedError("Mem0 SDK integration is not wired yet.")

