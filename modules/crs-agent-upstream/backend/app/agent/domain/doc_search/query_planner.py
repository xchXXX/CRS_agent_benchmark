"""LLM-backed query planner for doc_search."""

from __future__ import annotations

import logging
from typing import Any

from app.agent.model_ids import normalize_configured_model
from app.agent.domain.doc_search.models import DocSearchPlannedQuery, DocSearchQueryPlan
from app.agent.domain.doc_search.prompts import (
    DOC_SEARCH_QUERY_PLANNER_INSTRUCTIONS,
    DOC_SEARCH_QUERY_PLANNER_PROMPT,
)
from app.core.config import Settings, settings as app_settings

logger = logging.getLogger(__name__)


class PydanticAIDocSearchQueryPlanner:
    """Plan search-like queries from user intent and image evidence."""

    def __init__(
        self,
        *,
        config_service: Any | None = None,
        settings: Settings | None = None,
        model_override: Any | None = None,
    ):
        self._config_service = config_service
        self._settings = settings or app_settings
        self._model_override = model_override
        self._agent = None
        self._agent_signature: tuple[Any, int, float, float] | None = None

    async def plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
    ) -> DocSearchQueryPlan | None:
        raw_model = self._model_override
        if raw_model is None:
            raw_model = (
                self._get_config("openrouter_clarify_model", None)
                or self._get_config("agent_model", self._settings.agent_model)
            )
        model = normalize_configured_model(raw_model)
        if not model or model == "test":
            return None

        prompt = DOC_SEARCH_QUERY_PLANNER_PROMPT.format(
            query=(query or "").strip() or "无",
            image_evidence=(image_evidence or "").strip() or "无",
            known_slots=(known_slots or "").strip() or "无",
        )

        max_tokens = int(self._get_config("llm_clarify_max_tokens", 1024))
        temperature = float(self._get_config("llm_clarify_temperature", 0.1))
        timeout = float(self._get_config("llm_clarify_timeout", 15))

        try:
            agent = self._get_agent(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            response = await agent.run(user_prompt=prompt)
        except Exception as exc:
            logger.warning("doc_search query planner failed, fallback to original query. reason=%s", exc)
            return None

        plan = response.output
        if isinstance(plan, DocSearchQueryPlan):
            parsed_plan = plan
        else:
            try:
                parsed_plan = DocSearchQueryPlan.model_validate(plan)
            except Exception:
                return None

        normalized_queries: list[DocSearchPlannedQuery] = []
        seen: set[str] = set()
        for item in parsed_plan.queries:
            text = str(item.query or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized_queries.append(
                DocSearchPlannedQuery(
                    query=text,
                    intent="doc_search",
                    confidence=float(item.confidence or 0.5),
                )
            )

        primary_query = str(parsed_plan.primary_query or "").strip()
        if primary_query and primary_query.lower() not in seen:
            normalized_queries.insert(
                0,
                DocSearchPlannedQuery(query=primary_query, intent="doc_search", confidence=0.95),
            )
            seen.add(primary_query.lower())

        if not normalized_queries:
            return None

        return DocSearchQueryPlan(
            primary_query=normalized_queries[0].query,
            queries=normalized_queries[:6],
            rationale=str(parsed_plan.rationale or "").strip(),
        )

    def _get_agent(
        self,
        *,
        model: Any,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, max_tokens, temperature, timeout)
        if self._agent is not None and self._agent_signature == signature:
            return self._agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._agent = Agent(
            model=model,
            output_type=DocSearchQueryPlan,
            instructions=DOC_SEARCH_QUERY_PLANNER_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=1,
            output_retries=1,
            defer_model_check=True,
        )
        self._agent_signature = signature
        return self._agent

    def _get_config(self, key: str, default: Any) -> Any:
        if self._config_service is None:
            return default
        return self._config_service.get(key, default)
