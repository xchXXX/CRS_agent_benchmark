"""Keyword resolution for circuit-diagram body search."""

from __future__ import annotations

from typing import Any


def resolve_circuit_body_keyword(
    *,
    search_data: dict[str, Any] | None,
    fallback_query: str,
) -> str:
    """Resolve the keyword sent to the body-search service.

    First version reuses existing search query fields. Query-planner output can
    later write a dedicated body keyword without changing runtime wiring.
    """

    data = search_data or {}
    candidates = [
        data.get("body_keyword"),
        data.get("circuit_body_keyword"),
        data.get("original_query"),
        data.get("query"),
        fallback_query,
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""
