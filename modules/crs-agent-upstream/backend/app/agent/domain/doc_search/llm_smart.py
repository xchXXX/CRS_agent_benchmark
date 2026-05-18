"""Pydantic AI backed llm_smart service for doc_search."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.agent.model_ids import normalize_configured_model
from app.agent.domain.doc_search.matching import DocSearchResultMatcher
from app.agent.domain.doc_search.models import (
    DocSearchLLMClarifyOption,
    DocSearchLLMClarifyResult,
)
from app.agent.domain.doc_search.prompts import DOC_SEARCH_LLM_CLARIFY_PROMPT
from app.agent.domain.doc_search.prompts import DOC_SEARCH_LLM_CLARIFY_INSTRUCTIONS
from app.core.config import Settings, settings as app_settings


logger = logging.getLogger(__name__)


class _LLMClarifyPromptOption(BaseModel):
    label: str
    description: str = ""
    doc_indices: list[int] = Field(default_factory=list)


class _LLMClarifyPromptOutput(BaseModel):
    question: str = "请选择："
    dimension: str = ""
    options: list[_LLMClarifyPromptOption] = Field(default_factory=list)


class PydanticAIDocSearchLLMClarifyService:
    """Default llm_smart implementation aligned with the new Pydantic AI runtime."""

    MAX_DOCS_FOR_LLM = 30
    _SCOPED_FACETS = ("brand", "series", "model", "doc_type", "emissions", "subsystem", "ecu", "supplier", "eng_code")
    _FALLBACK_TEXT_FIELDS = ("filename", "title", "hierarchy_full")
    _OTHER_LIKE_TOKENS = ("其他", "不确定")

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
        self._matcher = DocSearchResultMatcher()

    async def analyze(
        self,
        *,
        results: list[dict[str, Any]],
        query: str,
        existing_filters: dict[str, Any],
        user_intent_entities: dict[str, list[str]] | None = None,
    ) -> DocSearchLLMClarifyResult | None:
        if not self._get_config("llm_clarify_enabled", True):
            return None

        min_results = int(self._get_config("llm_clarify_min_results", 5))
        if len(results) <= min_results:
            return None

        raw_model = self._model_override
        if raw_model is None:
            raw_model = self._get_config(
                "openrouter_clarify_model",
                None,
            ) or self._get_config("agent_model", self._settings.agent_model)
        model = self._normalize_model(raw_model)
        if not model or model == "test":
            logger.info("llm_smart skipped because no runnable clarify model is configured.")
            return None

        scoped_results = self._scope_results_by_explicit_constraints(
            results=results,
            existing_filters=existing_filters,
            user_intent_entities=user_intent_entities,
        )
        if len(scoped_results) < 2:
            return None

        prompt = self._build_prompt(
            results=scoped_results,
            query=query,
            existing_filters=existing_filters,
            user_intent_entities=user_intent_entities,
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
            logger.warning("llm_smart execution failed, fallback to direct display. reason=%s", exc)
            return None

        return self._build_result(response.output, scoped_results)

    def _scope_results_by_explicit_constraints(
        self,
        *,
        results: list[dict[str, Any]],
        existing_filters: dict[str, Any],
        user_intent_entities: dict[str, list[str]] | None,
    ) -> list[dict[str, Any]]:
        scoped = list(results)
        for facet in self._SCOPED_FACETS:
            explicit_choice = self._resolve_explicit_choice(
                facet=facet,
                existing_filters=existing_filters,
                user_intent_entities=user_intent_entities,
            )
            if not explicit_choice:
                continue

            narrowed = [
                item for item in scoped
                if self._matches_scope_facet(item, facet, explicit_choice)
            ]
            if len(narrowed) >= 2:
                scoped = narrowed

        return scoped

    def _resolve_explicit_choice(
        self,
        *,
        facet: str,
        existing_filters: dict[str, Any],
        user_intent_entities: dict[str, list[str]] | None,
    ) -> str | None:
        explicit = existing_filters.get(facet)
        if explicit:
            return str(explicit)

        values = [
            str(item)
            for item in (user_intent_entities or {}).get(facet) or []
            if str(item).strip()
        ]
        if not values:
            return None
        if len(values) == 1:
            return values[0]

        if facet != "doc_type":
            return None

        narrowed = self._narrow_specific_values(values)
        if len(narrowed) == 1:
            return narrowed[0]
        return None

    def _narrow_specific_values(self, values: list[str]) -> list[str]:
        normalized = {
            value: self._matcher.normalize_for_compare(value)
            for value in values
        }
        removable: set[str] = set()
        for value in values:
            current = normalized[value]
            if not current:
                continue
            for other in values:
                if value == other:
                    continue
                candidate = normalized[other]
                if not candidate or current == candidate:
                    continue
                if current in candidate and len(current) < len(candidate):
                    removable.add(value)
                    break

        narrowed = [value for value in values if value not in removable]
        return narrowed

    def _matches_scope_facet(
        self,
        result: dict[str, Any],
        facet: str,
        choice: str,
    ) -> bool:
        if self._matcher.matches_facet(result, facet, choice):
            return True

        choice_norm = self._matcher.normalize_for_compare(choice)
        if not choice_norm:
            return False

        for field_name in self._FALLBACK_TEXT_FIELDS:
            raw_value = result.get(field_name)
            if raw_value and choice_norm in self._matcher.normalize_for_compare(str(raw_value)):
                return True
        return False

    def _build_prompt(
        self,
        *,
        results: list[dict[str, Any]],
        query: str,
        existing_filters: dict[str, Any],
        user_intent_entities: dict[str, list[str]] | None,
    ) -> str:
        filter_name_map = {
            "brand": "品牌",
            "series": "系列",
            "model": "型号",
            "doc_type": "资料类型",
            "ecu": "ECU",
            "emissions": "排放标准",
            "subsystem": "子系统",
            "supplier": "供应商",
            "eng_code": "工程码",
        }
        filter_parts = [
            f"{filter_name_map.get(key, key)}={value}"
            for key, value in existing_filters.items()
            if not str(key).startswith("_") and value not in (None, "")
        ]
        filters_text = "、".join(filter_parts) if filter_parts else "无"

        hint_labels = {
            "brand": "用户要找的品牌",
            "series": "用户要找的系列",
            "model": "用户要找的型号",
            "doc_type": "用户要找的资料类型",
            "ecu": "用户关心的ECU",
            "subsystem": "用户关心的子系统",
            "supplier": "用户关心的供应商",
            "emissions": "用户关心的排放标准",
            "eng_code": "用户提到的工程码",
        }
        hints: list[str] = []
        for key, label in hint_labels.items():
            if key in existing_filters:
                continue
            values = (user_intent_entities or {}).get(key) or []
            if values:
                hints.append(f"- {label}：{', '.join(str(item) for item in values)}")
        intent_hints = "\n".join(hints) if hints else "无额外意图线索"

        document_lines: list[str] = []
        for index, item in enumerate(results[: self.MAX_DOCS_FOR_LLM]):
            filename = str(item.get("filename") or item.get("title") or "未知")
            if len(filename) > 150:
                filename = f"{filename[:150]}..."
            document_lines.append(f"{index}. {filename}")
        documents_text = "\n".join(document_lines)

        return DOC_SEARCH_LLM_CLARIFY_PROMPT.format(
            query=query or "未提供",
            filters=filters_text,
            intent_hints=intent_hints,
            documents=documents_text,
        )

    def _build_result(
        self,
        parsed: _LLMClarifyPromptOutput,
        results: list[dict[str, Any]],
    ) -> DocSearchLLMClarifyResult | None:
        if not parsed.options or len(parsed.options) < 2:
            return None

        option_rows: list[tuple[str, str | None, list[str]]] = []
        for item in parsed.options:
            if not item.label:
                continue

            file_ids: list[str] = []
            for index in item.doc_indices:
                if 0 <= index < len(results):
                    file_id = results[index].get("file_id")
                    if file_id not in (None, ""):
                        file_id_text = str(file_id)
                        file_ids.append(file_id_text)
            option_rows.append((item.label, item.description or None, file_ids))

        total_result_ids = [
            str(item.get("file_id"))
            for item in results[: self.MAX_DOCS_FOR_LLM]
            if item.get("file_id") not in (None, "")
        ]
        covered_ids = {
            file_id
            for _label, _description, file_ids in option_rows
            for file_id in file_ids
        }

        options: list[DocSearchLLMClarifyOption] = []
        for label, description, file_ids in option_rows:
            normalized_file_ids = list(dict.fromkeys(file_ids))
            if not normalized_file_ids and self._is_other_like_label(label):
                normalized_file_ids = [
                    file_id for file_id in total_result_ids
                    if file_id not in covered_ids
                ]
                covered_ids.update(normalized_file_ids)

            if not normalized_file_ids:
                continue

            options.append(
                DocSearchLLMClarifyOption(
                    label=label,
                    description=description,
                    file_ids=normalized_file_ids,
                )
            )

        if len(options) < 2:
            return None

        final_covered_ids = {
            file_id
            for option in options
            for file_id in option.file_ids
        }
        coverage = len(final_covered_ids) / len(set(total_result_ids)) if total_result_ids else 0
        if coverage < 0.5:
            logger.warning("llm_smart coverage too low: %.1f%%", coverage * 100)
            return None

        return DocSearchLLMClarifyResult(
            question=parsed.question or "请选择：",
            dimension=parsed.dimension or "",
            reason="llm_smart_clarify",
            options=options,
        )

    def _is_other_like_label(self, label: str) -> bool:
        normalized = self._matcher.normalize_for_compare(label)
        return any(token in normalized for token in self._OTHER_LIKE_TOKENS)

    def _get_config(self, key: str, default: Any) -> Any:
        if self._config_service is None:
            return default
        return self._config_service.get(key, default)

    def _normalize_model(self, model: Any) -> Any:
        return normalize_configured_model(model)

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
            output_type=_LLMClarifyPromptOutput,
            instructions=DOC_SEARCH_LLM_CLARIFY_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=2,
            output_retries=2,
            defer_model_check=True,
        )
        self._agent_signature = signature
        return self._agent
