"""Translate internal runtime events to frontend-friendly payloads."""

from typing import Any, Dict

from app.agent.models.events import AgentEventType, AgentRuntimeEvent


class FrontendProtocolAdapter:
    def to_event(self, event: AgentRuntimeEvent) -> Dict[str, Any]:
        if event.type == AgentEventType.START:
            return {"type": "start", "session_id": event.session_id, **event.metadata}

        if event.type == AgentEventType.HINT:
            return {
                "type": "hint",
                "session_id": event.session_id,
                "message": event.message or "",
                **event.metadata,
            }

        if event.type == AgentEventType.TEXT_DELTA:
            return {
                "type": "chunk",
                "session_id": event.session_id,
                "content": event.content or "",
            }

        if event.type == AgentEventType.TOOL_STATUS:
            return {
                "type": "tool_status",
                "session_id": event.session_id,
                "tool_name": event.tool_name,
                "status": event.status,
                "message": event.message,
                **event.metadata,
            }

        if event.type == AgentEventType.ASK_USER:
            ask_user = event.ask_user
            return {
                "type": "ask_user",
                "session_id": event.session_id,
                "tool_call_id": ask_user.tool_call_id if ask_user else None,
                "question": ask_user.question if ask_user else event.message,
                "input_type": ask_user.input_type.value if ask_user else None,
                "options": [item.model_dump() for item in ask_user.options] if ask_user else [],
                "allow_free_input": ask_user.allow_free_input if ask_user else False,
                "input_hint": ask_user.input_hint if ask_user else None,
                "unit": ask_user.unit if ask_user else None,
                "reference_range": ask_user.reference_range if ask_user else None,
                "context": ask_user.context if ask_user else {},
            }

        if event.type == AgentEventType.DONE:
            return {"type": "done", "session_id": event.session_id, **event.metadata}

        if event.type == AgentEventType.FALLBACK:
            return {"type": "fallback", "session_id": event.session_id, **event.metadata}

        return {
            "type": "error",
            "session_id": event.session_id,
            "message": event.message or "Unknown runtime error",
            **event.metadata,
        }
