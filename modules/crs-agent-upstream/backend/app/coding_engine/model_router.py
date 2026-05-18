"""Model access for coding-engine nodes."""

from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter

from app.coding_engine.config import load_runtime_env


load_runtime_env()


def _model_name() -> str:
    configured_model = (
        os.getenv("CRS_CODING_ENGINE_MODEL")
        or os.getenv("CRS_AGENT_MODEL")
        or "openrouter:google/gemini-3.1-flash-lite-preview"
    )
    if configured_model.startswith("openrouter:"):
        return configured_model.removeprefix("openrouter:")
    return configured_model


def has_model_key() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def invoke_coding_model(system_prompt: str, user_prompt: str, *, max_tokens: int = 2048) -> str:
    if not has_model_key():
        return ""

    model = ChatOpenRouter(
        model=_model_name(),
        temperature=0.2,
        max_tokens=max_tokens,
        max_retries=2,
    )
    response = model.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content)

