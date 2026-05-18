"""LLM-backed normalization for parameter-query requests."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.model_ids import normalize_configured_model
from app.agent.domain.parameter_query.normalizer import (
    extract_pin_token,
    extract_requested_field,
    normalize_free_text_hint,
    normalize_pin_no,
    normalize_text,
    remove_known_terms,
)
from app.agent.domain.parameter_query.prompts import (
    PARAM_QUERY_INTENT_INSTRUCTIONS,
    PARAM_QUERY_ROW_MATCH_INSTRUCTIONS,
)
from app.core.config import Settings, settings as app_settings


logger = logging.getLogger(__name__)


class ParameterQuerySourceCandidate(BaseModel):
    source_id: int
    title: str
    ecu_name: str | None = None
    system_voltage: int | None = None
    row_count: int = 0
    aliases: list[str] = Field(default_factory=list)


class ParameterQueryIntent(BaseModel):
    ecu_source_id: int | None = None
    candidate_source_ids: list[int] = Field(default_factory=list)
    ecu_text: str | None = None
    source_clue_text: str | None = None
    component_text: str | None = None
    pin_text: str | None = None
    requested_parameter_text: str | None = None
    requested_field: str | None = None
    target_text: str | None = None
    target_type: Literal["ecu_pin_no", "connector_pin_no", "signal", "component", "unknown"] = "unknown"
    need_clarify: bool = False
    clarify_target: Literal["ecu", "target", "none"] = "none"
    reason: str = ""


class ParameterQueryRowCandidate(BaseModel):
    row_id: int
    ecu_pin_no: str | None = None
    component_name: str | None = None
    pin_definition: str | None = None
    connector_pin_no: str | None = None
    open_voltage_text: str | None = None
    static_voltage_text: str | None = None
    idle_voltage_text: str | None = None
    remark: str | None = None


class ParameterQueryRowSelection(BaseModel):
    match_state: Literal["exact_match", "multiple_candidates", "pin_not_found", "missing_target"] = "pin_not_found"
    row_ids: list[int] = Field(default_factory=list)
    reason: str = ""


class PydanticAIParameterQueryNormalizer:
    """Normalize oral parameter requests into structured slots with LLM first."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        config_service: Any | None = None,
        model_override: Any | None = None,
    ) -> None:
        self._settings = settings or app_settings
        self._config_service = config_service
        self._model_override = model_override
        self._intent_agent = None
        self._row_agent = None
        self._intent_agent_signature: tuple[Any, int, float, float] | None = None
        self._row_agent_signature: tuple[Any, int, float, float] | None = None

    def interpret_query(
        self,
        *,
        query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
        selected_source_id: int | None = None,
        selected_source_title: str | None = None,
    ) -> ParameterQueryIntent:
        model = self._resolve_model()
        if not model or model == "test":
            return self._fallback_intent(
                query=query,
                candidate_sources=candidate_sources,
                selected_source_id=selected_source_id,
                selected_source_title=selected_source_title,
            )

        prompt = self._build_intent_prompt(
            query=query,
            candidate_sources=candidate_sources,
            selected_source_id=selected_source_id,
            selected_source_title=selected_source_title,
        )
        try:
            agent = self._get_intent_agent(model=model, max_tokens=1200, temperature=0.1, timeout=20.0)
            result = agent.run_sync(user_prompt=prompt)
            return self._normalize_intent_output(result.output)
        except Exception as exc:
            logger.warning("parameter_query intent normalization failed, fallback to heuristic. reason=%s", exc)
            return self._fallback_intent(
                query=query,
                candidate_sources=candidate_sources,
                selected_source_id=selected_source_id,
                selected_source_title=selected_source_title,
            )

    async def interpret_query_async(
        self,
        *,
        query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
        selected_source_id: int | None = None,
        selected_source_title: str | None = None,
    ) -> ParameterQueryIntent:
        model = self._resolve_model()
        if not model or model == "test":
            return self._fallback_intent(
                query=query,
                candidate_sources=candidate_sources,
                selected_source_id=selected_source_id,
                selected_source_title=selected_source_title,
            )

        prompt = self._build_intent_prompt(
            query=query,
            candidate_sources=candidate_sources,
            selected_source_id=selected_source_id,
            selected_source_title=selected_source_title,
        )
        try:
            agent = self._get_intent_agent(model=model, max_tokens=1200, temperature=0.1, timeout=20.0)
            result = await agent.run(user_prompt=prompt)
            return self._normalize_intent_output(result.output)
        except Exception as exc:
            logger.warning("parameter_query intent normalization failed, fallback to heuristic. reason=%s", exc)
            return self._fallback_intent(
                query=query,
                candidate_sources=candidate_sources,
                selected_source_id=selected_source_id,
                selected_source_title=selected_source_title,
            )

    def select_rows(
        self,
        *,
        query: str,
        source_title: str,
        source_ecu_name: str | None,
        requested_field: str | None,
        target_text: str | None,
        target_type: str,
        rows: list[ParameterQueryRowCandidate],
    ) -> ParameterQueryRowSelection:
        model = self._resolve_model()
        if not model or model == "test":
            return self._fallback_row_selection(
                target_text=target_text,
                target_type=target_type,
                rows=rows,
            )

        prompt = self._build_row_prompt(
            query=query,
            source_title=source_title,
            source_ecu_name=source_ecu_name,
            requested_field=requested_field,
            target_text=target_text,
            target_type=target_type,
            rows=rows,
        )
        try:
            agent = self._get_row_agent(model=model, max_tokens=1200, temperature=0.05, timeout=20.0)
            result = agent.run_sync(user_prompt=prompt)
            return result.output
        except Exception as exc:
            logger.warning("parameter_query row matching failed, fallback to heuristic. reason=%s", exc)
            return self._fallback_row_selection(
                target_text=target_text,
                target_type=target_type,
                rows=rows,
            )

    async def select_rows_async(
        self,
        *,
        query: str,
        source_title: str,
        source_ecu_name: str | None,
        requested_field: str | None,
        target_text: str | None,
        target_type: str,
        rows: list[ParameterQueryRowCandidate],
    ) -> ParameterQueryRowSelection:
        model = self._resolve_model()
        if not model or model == "test":
            return self._fallback_row_selection(
                target_text=target_text,
                target_type=target_type,
                rows=rows,
            )

        prompt = self._build_row_prompt(
            query=query,
            source_title=source_title,
            source_ecu_name=source_ecu_name,
            requested_field=requested_field,
            target_text=target_text,
            target_type=target_type,
            rows=rows,
        )
        try:
            agent = self._get_row_agent(model=model, max_tokens=1200, temperature=0.05, timeout=20.0)
            result = await agent.run(user_prompt=prompt)
            return result.output
        except Exception as exc:
            logger.warning("parameter_query row matching failed, fallback to heuristic. reason=%s", exc)
            return self._fallback_row_selection(
                target_text=target_text,
                target_type=target_type,
                rows=rows,
            )

    def _resolve_model(self) -> Any:
        raw_model = self._model_override
        if raw_model is None:
            raw_model = self._get_config("agent_model", self._settings.agent_model)
        return self._normalize_model(raw_model)

    def _get_config(self, key: str, default: Any) -> Any:
        if self._config_service is None:
            return default
        return self._config_service.get(key, default)

    def _build_intent_prompt(
        self,
        *,
        query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
        selected_source_id: int | None,
        selected_source_title: str | None,
    ) -> str:
        candidate_lines = []
        for item in candidate_sources:
            alias_text = ", ".join(item.aliases[:8]) if item.aliases else "无"
            voltage_text = f"{item.system_voltage}V" if item.system_voltage is not None else "未知电压"
            candidate_lines.append(
                f"- source_id={item.source_id} | ecu={item.ecu_name or '未知'} | title={item.title} | "
                f"voltage={voltage_text} | rows={item.row_count} | aliases={alias_text}"
            )
        source_text = "\n".join(candidate_lines) if candidate_lines else "无可用 ECU 候选资料"
        selected_text = (
            f"已锁定 source_id={selected_source_id}, title={selected_source_title or ''}"
            if selected_source_id is not None
            else "当前没有已锁定的 ECU 资料"
        )
        return (
            f"用户问题：{query}\n"
            f"{selected_text}\n"
            "请先完成：source clue / component target / pin token / requested parameter 的整句角色识别，再输出结构化结果。\n"
            "支持的 requested_field: pin_definition, ecu_pin_no, connector_pin_no, voltage, open_voltage, static_voltage, idle_voltage, remark\n"
            "候选 ECU 资料如下：\n"
            f"{source_text}"
        )

    def _build_row_prompt(
        self,
        *,
        query: str,
        source_title: str,
        source_ecu_name: str | None,
        requested_field: str | None,
        target_text: str | None,
        target_type: str,
        rows: list[ParameterQueryRowCandidate],
    ) -> str:
        row_lines = []
        for row in rows:
            voltages = " / ".join(
                item for item in [row.open_voltage_text, row.static_voltage_text, row.idle_voltage_text] if item
            )
            row_lines.append(
                f"- row_id={row.row_id} | ecu_pin={row.ecu_pin_no or '无'} | component={row.component_name or '无'} | "
                f"definition={row.pin_definition or '无'} | connector_pin={row.connector_pin_no or '无'} | "
                f"voltages={voltages or '无'} | remark={row.remark or '无'}"
            )
        rows_text = "\n".join(row_lines) if row_lines else "无可用行"
        return (
            f"用户问题：{query}\n"
            f"已确认 ECU：{source_ecu_name or source_title}\n"
            f"资料标题：{source_title}\n"
            f"requested_field={requested_field or '未指定'}\n"
            f"target_type={target_type}\n"
            f"target_text={target_text or '未提取到'}\n"
            "该 ECU 的针脚行如下：\n"
            f"{rows_text}"
        )

    def _fallback_intent(
        self,
        *,
        query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
        selected_source_id: int | None,
        selected_source_title: str | None,
    ) -> ParameterQueryIntent:
        requested_field = extract_requested_field(query)
        normalized_query = normalize_text(query)
        explicit_pin = extract_pin_token(query)

        if requested_field is None:
            if any(token in query for token in ("作用", "定义", "什么意思")):
                requested_field = "pin_definition"
            elif any(token in query for token in ("哪个针脚", "在哪个针脚", "几号脚", "脚位")):
                requested_field = "ecu_pin_no"

        if selected_source_id is not None:
            return ParameterQueryIntent(
                ecu_source_id=selected_source_id,
                ecu_text=selected_source_title,
                requested_field=requested_field,
                target_text=explicit_pin or self._fallback_target_text(query, candidate_sources, selected_source_id),
                target_type="ecu_pin_no" if explicit_pin else self._fallback_target_type(query, explicit_pin),
                need_clarify=False,
                clarify_target="none",
                reason="selected_source_locked",
            )

        ranked_sources = self._rank_source_candidates(query=query, candidate_sources=candidate_sources)
        best_source_id = ranked_sources[0] if ranked_sources else None
        exact_source_id = self._match_exact_source_id(normalized_query, candidate_sources)
        ecu_source_id = exact_source_id or best_source_id
        ecu_text = None
        if ecu_source_id is not None:
            matched_source = next((item for item in candidate_sources if item.source_id == ecu_source_id), None)
            ecu_text = matched_source.ecu_name or matched_source.title if matched_source else None

        if ecu_source_id is None:
            return ParameterQueryIntent(
                ecu_source_id=None,
                candidate_source_ids=ranked_sources[:6],
                ecu_text=None,
                requested_field=requested_field,
                target_text=explicit_pin or self._fallback_target_text(query, candidate_sources, None),
                target_type="ecu_pin_no" if explicit_pin else self._fallback_target_type(query, explicit_pin),
                need_clarify=True,
                clarify_target="ecu",
                reason="ecu_missing_in_query",
            )

        return ParameterQueryIntent(
            ecu_source_id=ecu_source_id,
            candidate_source_ids=ranked_sources[:6],
            ecu_text=ecu_text,
            requested_field=requested_field,
            target_text=explicit_pin or self._fallback_target_text(query, candidate_sources, ecu_source_id),
            target_type="ecu_pin_no" if explicit_pin else self._fallback_target_type(query, explicit_pin),
            need_clarify=False,
            clarify_target="none",
            reason="heuristic_source_match",
        )

    @staticmethod
    def _normalize_intent_output(intent: ParameterQueryIntent) -> ParameterQueryIntent:
        updates: dict[str, Any] = {}

        if not intent.target_text:
            if intent.target_type == "component" and intent.component_text:
                updates["target_text"] = intent.component_text
            elif intent.target_type in {"ecu_pin_no", "connector_pin_no"} and intent.pin_text:
                updates["target_text"] = intent.pin_text

        if intent.target_type == "unknown":
            if intent.component_text and not intent.pin_text:
                updates["target_type"] = "component"
                updates.setdefault("target_text", intent.component_text)
            elif intent.pin_text:
                updates["target_type"] = "ecu_pin_no"
                updates.setdefault("target_text", intent.pin_text)

        if not intent.requested_field and intent.requested_parameter_text:
            normalized_parameter = normalize_text(intent.requested_parameter_text)
            if normalized_parameter in {"电压", "几伏", "多少伏", "电压多少"}:
                updates["requested_field"] = "voltage"

        if updates:
            return intent.model_copy(update=updates)
        return intent

    def _fallback_row_selection(
        self,
        *,
        target_text: str | None,
        target_type: str,
        rows: list[ParameterQueryRowCandidate],
    ) -> ParameterQueryRowSelection:
        if target_type == "ecu_pin_no" and target_text:
            normalized_pin = normalize_pin_no(target_text)
            matched = [
                row.row_id for row in rows
                if normalize_pin_no(row.ecu_pin_no) == normalized_pin
            ]
            if not matched:
                return ParameterQueryRowSelection(match_state="pin_not_found", row_ids=[], reason="pin_not_found")
            if len(matched) == 1:
                return ParameterQueryRowSelection(match_state="exact_match", row_ids=matched, reason="pin_exact_match")
            return ParameterQueryRowSelection(
                match_state="multiple_candidates",
                row_ids=matched[:6],
                reason="multiple_rows_same_pin",
            )

        normalized_target = normalize_text(target_text)
        if not normalized_target:
            return ParameterQueryRowSelection(match_state="missing_target", row_ids=[], reason="missing_target")

        exact = [
            row.row_id for row in rows
            if normalize_text(row.pin_definition) == normalized_target
            or normalize_text(row.component_name) == normalized_target
            or normalize_text(row.connector_pin_no) == normalized_target
        ]
        if len(exact) == 1:
            return ParameterQueryRowSelection(match_state="exact_match", row_ids=exact, reason="exact_text_match")
        if len(exact) > 1:
            return ParameterQueryRowSelection(
                match_state="multiple_candidates",
                row_ids=exact[:6],
                reason="multiple_exact_text_match",
            )

        contains = [
            row.row_id for row in rows
            if normalized_target in normalize_text(row.pin_definition)
            or normalized_target in normalize_text(row.component_name)
            or normalized_target in normalize_text(row.connector_pin_no)
        ]
        if len(contains) == 1:
            return ParameterQueryRowSelection(match_state="exact_match", row_ids=contains, reason="contains_match")
        if len(contains) > 1:
            return ParameterQueryRowSelection(
                match_state="multiple_candidates",
                row_ids=contains[:6],
                reason="multiple_contains_match",
            )
        return ParameterQueryRowSelection(match_state="pin_not_found", row_ids=[], reason="row_not_found")

    @staticmethod
    def _fallback_target_type(query: str, explicit_pin: str | None) -> str:
        if explicit_pin:
            return "ecu_pin_no"
        if any(token in query for token in ("接插件针脚", "插头针脚", "接插件脚号")):
            return "connector_pin_no"
        if any(token in normalize_text(query) for token in ("canh", "canl", "接地", "供电", "信号", "lin", "k线")):
            return "signal"
        return "component"

    @staticmethod
    def _fallback_target_text(
        query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
        source_id: int | None,
    ) -> str | None:
        known_terms: list[str] = []
        for source in candidate_sources:
            if source_id is not None and source.source_id != source_id:
                continue
            if source.ecu_name:
                known_terms.append(normalize_text(source.ecu_name))
            known_terms.extend(normalize_text(alias) for alias in source.aliases)
            if source_id is not None:
                break
        if any(token in query for token in ("作用", "定义", "什么意思")):
            known_terms.extend(normalize_text(token) for token in ("作用", "定义", "什么意思", "引脚", "针脚"))
        if any(token in query for token in ("哪个针脚", "在哪个针脚", "几号脚", "脚位")):
            known_terms.extend(normalize_text(token) for token in ("哪个针脚", "在哪个针脚", "几号脚", "脚位"))
        stripped = remove_known_terms(normalize_text(query), [term for term in known_terms if term])
        return normalize_free_text_hint(stripped)

    @staticmethod
    def _match_exact_source_id(
        normalized_query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
    ) -> int | None:
        best_id = None
        best_len = -1
        for source in candidate_sources:
            tokens = [normalize_text(source.ecu_name), normalize_text(source.title)]
            tokens.extend(normalize_text(alias) for alias in source.aliases)
            for token in tokens:
                if not token:
                    continue
                if token in normalized_query and len(token) > best_len:
                    best_id = source.source_id
                    best_len = len(token)
        return best_id

    @staticmethod
    def _rank_source_candidates(
        *,
        query: str,
        candidate_sources: list[ParameterQuerySourceCandidate],
    ) -> list[int]:
        normalized_query = normalize_text(query)
        scored: list[tuple[float, int]] = []
        for source in candidate_sources:
            score = 0.0
            terms = [normalize_text(source.ecu_name), normalize_text(source.title)]
            terms.extend(normalize_text(alias) for alias in source.aliases)
            best_match_len = max((len(term) for term in terms if term and term in normalized_query), default=0)
            if best_match_len:
                score += 200.0 + best_match_len
            if source.system_voltage is not None and f"{source.system_voltage}v" in normalized_query:
                score += 18.0
            if score > 0:
                scored.append((score, source.source_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [source_id for _, source_id in scored]

    @staticmethod
    def _normalize_model(model: Any) -> Any:
        return normalize_configured_model(model)

    def _get_intent_agent(
        self,
        *,
        model: Any,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, max_tokens, temperature, timeout)
        if self._intent_agent is not None and self._intent_agent_signature == signature:
            return self._intent_agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._intent_agent = Agent(
            model=model,
            output_type=ParameterQueryIntent,
            instructions=PARAM_QUERY_INTENT_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=2,
            output_retries=2,
            defer_model_check=True,
        )
        self._intent_agent_signature = signature
        return self._intent_agent

    def _get_row_agent(
        self,
        *,
        model: Any,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, max_tokens, temperature, timeout)
        if self._row_agent is not None and self._row_agent_signature == signature:
            return self._row_agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._row_agent = Agent(
            model=model,
            output_type=ParameterQueryRowSelection,
            instructions=PARAM_QUERY_ROW_MATCH_INSTRUCTIONS,
            model_settings=ModelSettings(
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            ),
            retries=2,
            output_retries=2,
            defer_model_check=True,
        )
        self._row_agent_signature = signature
        return self._row_agent
