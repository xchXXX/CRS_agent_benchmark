"""LLM reranking for circuit body-search location candidates."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.domain.circuit_body_search.models import CircuitBodyBestHit
from app.agent.model_ids import normalize_configured_model
from app.core.config import Settings, settings as app_settings


logger = logging.getLogger(__name__)


class CircuitBodyHitRerankItem(BaseModel):
    candidate_id: str
    rank: int
    confidence: Literal["high", "medium", "low"] = "medium"
    reason: str = ""


class CircuitBodyHitRerankOutput(BaseModel):
    ranked_candidates: list[CircuitBodyHitRerankItem] = Field(default_factory=list)


class PydanticAICircuitBodyHitReranker:
    """Rank in-document circuit hit regions using text evidence only."""

    def __init__(
        self,
        *,
        config_service: Any | None = None,
        settings: Settings | None = None,
        model_override: Any | None = None,
    ) -> None:
        self._config_service = config_service
        self._settings = settings or app_settings
        self._model_override = model_override
        self._agent = None
        self._agent_signature: tuple[Any, int, float, float] | None = None

    async def rerank(
        self,
        *,
        query: str,
        document_title: str,
        candidates: list[CircuitBodyBestHit],
    ) -> CircuitBodyHitRerankOutput | None:
        if len(candidates) <= 1:
            return None
        if not self._get_bool("circuit_diagram_body_hit_rerank_enabled", True):
            return None

        raw_model = self._model_override
        if raw_model is None:
            raw_model = (
                self._get_config("openrouter_clarify_model", None)
                or self._get_config("agent_model", self._settings.agent_model)
            )
        model = normalize_configured_model(raw_model)
        if not model or model == "test":
            return None

        max_candidates = max(min(self._get_int("circuit_diagram_body_hit_rerank_max_candidates", 12), 20), 2)
        prompt = self._build_prompt(
            query=query,
            document_title=document_title,
            candidates=candidates[:max_candidates],
        )
        max_tokens = self._get_int("circuit_diagram_body_hit_rerank_max_tokens", 1200)
        temperature = self._get_float("circuit_diagram_body_hit_rerank_temperature", 0.0)
        timeout = self._get_float("circuit_diagram_body_hit_rerank_timeout", 12.0)

        try:
            agent = self._get_agent(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            response = await agent.run(user_prompt=prompt)
        except Exception as exc:
            logger.warning("circuit body hit rerank failed, fallback to rule order. reason=%s", exc)
            return None

        output = response.output
        if isinstance(output, CircuitBodyHitRerankOutput):
            return output
        try:
            return CircuitBodyHitRerankOutput.model_validate(output)
        except Exception:
            return None

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
            output_type=CircuitBodyHitRerankOutput,
            instructions=(
                "你是商用车电路图内部搜索的候选位置排序器。"
                "你只根据候选的 OCR 文本证据判断哪个区域最可能满足用户要找的位置。"
                "不要编造不存在的信息；不要输出解释性正文，只返回结构化排序。"
                "优先选择完整包含用户目标词、上下文出现相关线路/信号/端子说明、命中更集中且证据更具体的区域。"
            ),
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

    @staticmethod
    def _build_prompt(
        *,
        query: str,
        document_title: str,
        candidates: list[CircuitBodyBestHit],
    ) -> str:
        lines = [
            f"用户要找的图内内容：{query or '未知'}",
            f"文档：{document_title or '未知文档'}",
            "请对下面候选区域排序，返回所有 candidate_id，不要漏掉候选。",
            "",
            "候选区域：",
        ]
        for hit in candidates:
            matched = hit.matched_text or hit.snippet
            evidence = (hit.nearby_ocr_text or hit.context or hit.snippet or "")[:900]
            lines.extend(
                [
                    f"- candidate_id: {hit.candidate_id or hit.hit_id}",
                    f"  page_number: {hit.page_number}",
                    f"  matched_text: {matched}",
                    f"  hit_count: {len(hit.source_hit_ids) or 1}",
                    f"  rule_score: {round(float(hit.score or 0.0), 3)}",
                    f"  evidence: {evidence or '无'}",
                ]
            )
        return "\n".join(lines)

    def _get_config(self, key: str, default: Any) -> Any:
        if self._config_service is None:
            return default
        try:
            return self._config_service.get(key, default)
        except Exception:
            return default

    def _get_int(self, key: str, default: int) -> int:
        try:
            return int(self._get_config(key, default))
        except (TypeError, ValueError):
            return default

    def _get_float(self, key: str, default: float) -> float:
        try:
            return float(self._get_config(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self._get_config(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
