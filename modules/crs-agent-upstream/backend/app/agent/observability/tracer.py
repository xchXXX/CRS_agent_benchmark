"""Simple tracer for runtime events."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


@dataclass
class LoopTraceEntry:
    sequence_no: int
    event_type: str
    session_id: Optional[str]
    detail: Optional[str]
    payload: Dict[str, Any]
    created_at: str


class LoopTracer:
    def __init__(self) -> None:
        self._entries: list[LoopTraceEntry] = []
        self._sequence_no = 0

    def fork(self) -> "LoopTracer":
        return LoopTracer()

    def entries(self) -> list[LoopTraceEntry]:
        return list(self._entries)

    def trace(
        self,
        event_type: str,
        session_id: Optional[str],
        detail: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._sequence_no += 1
        self._entries.append(
            LoopTraceEntry(
                sequence_no=self._sequence_no,
                event_type=event_type,
                session_id=session_id,
                detail=detail,
                payload=dict(payload or {}),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        logger.info(
            "[CRSAgentTrace] session=%s type=%s detail=%s payload=%s",
            session_id,
            event_type,
            detail,
            payload or {},
        )
