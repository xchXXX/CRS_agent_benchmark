"""HTTP client for external circuit-diagram body search."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider


logger = logging.getLogger(__name__)


class CircuitBodySearchClient:
    """Client for the external POST /api/search body-search service."""

    def __init__(self, *, config_provider: CircuitBodySearchConfigProvider) -> None:
        self._config_provider = config_provider

    async def search(self, *, pdf_id: str, keyword: str) -> dict[str, Any]:
        config = self._config_provider.load()
        if not config.enabled or not config.search_url:
            return {"status": "disabled", "data": {}}

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout) as client:
                response = await client.post(
                    config.search_url,
                    json={"pdf_id": pdf_id, "keyword": keyword},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("Circuit body search request failed: %s", exc)
            return {
                "status": "failed",
                "error": "external_search_failed",
                "message": str(exc),
            }

        if not isinstance(data, dict):
            return {
                "status": "failed",
                "error": "invalid_external_response",
                "message": "response is not a JSON object",
            }
        return data
