"""LangGraph app for local Studio development."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter
from langgraph.graph import END, START, MessagesState, StateGraph


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _load_runtime_env() -> None:
    load_dotenv(BACKEND_DIR / ".env", override=False)
    load_dotenv(BACKEND_DIR / ".env.runtime", override=False)


def _openrouter_model_name() -> str:
    configured_model = os.getenv("CRS_AGENT_MODEL", "")
    if configured_model.startswith("openrouter:"):
        return configured_model.removeprefix("openrouter:")
    return configured_model or "google/gemini-3.1-flash-lite-preview"


_load_runtime_env()


def _build_openrouter_model() -> ChatOpenRouter:
    return ChatOpenRouter(
        model=_openrouter_model_name(),
        temperature=0.2,
        max_tokens=1024,
        max_retries=2,
    )


def openrouter_chat_node(state: MessagesState) -> dict[str, list[AIMessage]]:
    if not os.getenv("OPENROUTER_API_KEY"):
        return {
            "messages": [
                AIMessage(
                    content=(
                        "OpenRouter API key is not configured. "
                        "Set OPENROUTER_API_KEY in backend/.env.runtime."
                    )
                )
            ]
        }

    response = _build_openrouter_model().invoke(
        [
            SystemMessage(
                content=(
                    "你是 CRS Agent 的 LangGraph Studio 调试入口。"
                    "请用中文简洁回答，并说明这是通过 OpenRouter 调用的真实模型结果。"
                )
            ),
            *state["messages"],
        ]
    )
    return {"messages": [response]}


builder = StateGraph(MessagesState)
builder.add_node("openrouter_chat", openrouter_chat_node)
builder.add_edge(START, "openrouter_chat")
builder.add_edge("openrouter_chat", END)

graph = builder.compile()
