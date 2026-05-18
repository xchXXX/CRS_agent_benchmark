"""Helpers for frontend-only visibility controls."""

from __future__ import annotations

from typing import Any

from app.agent.models.events import AgentRuntimeEvent
from app.core.config import settings
from app.schemas.chat import ChatResponse


_HIDDEN_SOURCE_KEYS = {
    "repair_knowledge_sources",
    "repair_knowledge_primary_title",
    "source_refs",
}


def is_frontend_source_display_enabled(runtime_deps: Any) -> bool:
    enabled = settings.frontend_source_display_enabled
    config_service = getattr(runtime_deps, "config_service", None)
    if config_service is not None:
        enabled = bool(config_service.get("frontend_source_display_enabled", enabled))
    return enabled


def sanitize_chat_response(response: ChatResponse, runtime_deps: Any) -> ChatResponse:
    if is_frontend_source_display_enabled(runtime_deps):
        return response

    ask_user = response.ask_user
    sanitized_ask_user = None
    if ask_user is not None:
        sanitized_ask_user = ask_user.model_copy(
            update={"context": _strip_source_fields(ask_user.context)}
        )

    if response.type == "ask_user" and sanitized_ask_user is not None:
        sanitized_content = sanitized_ask_user.model_dump(mode="json")
    else:
        sanitized_content = _strip_source_fields(response.content)

    return response.model_copy(
        update={
            "content": sanitized_content,
            "metadata": _strip_source_fields(response.metadata),
            "ask_user": sanitized_ask_user,
        }
    )


def sanitize_agent_event(event: AgentRuntimeEvent, runtime_deps: Any) -> AgentRuntimeEvent:
    if is_frontend_source_display_enabled(runtime_deps):
        return event

    ask_user = event.ask_user
    sanitized_ask_user = None
    if ask_user is not None:
        sanitized_ask_user = ask_user.model_copy(
            update={"context": _strip_source_fields(ask_user.context)}
        )

    return event.model_copy(
        update={
            "metadata": _strip_source_fields(event.metadata),
            "ask_user": sanitized_ask_user,
        }
    )


def _strip_source_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_source_fields(item)
            for key, item in value.items()
            if key not in _HIDDEN_SOURCE_KEYS
        }
    if isinstance(value, list):
        return [_strip_source_fields(item) for item in value]
    return value
