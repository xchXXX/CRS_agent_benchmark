"""LLM-first request intent router with minimal fallback rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.model_ids import normalize_configured_model
from app.core.config import Settings, settings as app_settings


logger = logging.getLogger(__name__)


class RoutedIntent(str, Enum):
    DOC_SEARCH = "doc_search"
    PARAM_QUERY = "param_query"
    GENERAL_CHAT = "general_chat"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    FAULT_DIAGNOSIS_LLM = "fault_diagnosis_llm"


@dataclass(frozen=True)
class IntentDecision:
    intent: RoutedIntent
    reason: str
    source: str = "fallback_rule"
    normalized_fault_code: str | None = None
    confidence: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "reason": self.reason,
            "source": self.source,
            "normalized_fault_code": self.normalized_fault_code,
            "confidence": self.confidence,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "IntentDecision | None":
        if not isinstance(payload, dict):
            return None

        raw_intent = str(payload.get("intent") or "").strip()
        if not raw_intent:
            return None
        try:
            intent = RoutedIntent(raw_intent)
        except ValueError:
            return None

        confidence = payload.get("confidence")
        try:
            normalized_confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            normalized_confidence = None

        normalized_fault_code = payload.get("normalized_fault_code")
        return cls(
            intent=intent,
            reason=str(payload.get("reason") or "").strip() or "cached",
            source=str(payload.get("source") or "cached"),
            normalized_fault_code=str(normalized_fault_code).strip() or None
            if normalized_fault_code is not None
            else None,
            confidence=normalized_confidence,
        )


class _IntentClassifierOutput(BaseModel):
    intent: Literal["doc_search", "param_query", "general_chat", "fault_diagnosis"]
    reason: str = ""
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class RequestIntentRouter:
    """Small intent gate before requests enter dedicated workflows."""

    DOC_MATERIAL_KEYWORDS = {
        "电路图",
        "线路图",
        "线束图",
        "针脚图",
        "引脚图",
        "原理图",
        "手册",
        "维修手册",
        "资料",
        "维修资料",
        "文档",
        "图纸",
        "说明书",
    }
    DOC_ACTION_KEYWORDS = {"搜索", "查找", "帮我找", "找一下", "搜一下", "帮我搜", "查一下"}
    DOC_TARGET_HINTS = {"资料", "文档", "手册", "图纸", "电路图", "线路图", "原理图", "说明书"}
    DOC_BODY_SCOPE_HINTS = {
        "里面",
        "里边",
        "内部",
        "图内",
        "图里",
        "图中",
        "图上",
        "文档内",
        "文档里",
        "文档中",
        "资料内",
        "资料里",
        "资料中",
        "pdf内",
        "pdf里",
        "PDF内",
        "PDF里",
    }
    DOC_BODY_LOOKUP_HINTS = {"找", "查", "搜", "定位", "位置", "在哪", "在哪里", "哪一页", "第几页"}
    ECU_DATA_MATERIAL_KEYWORDS = {
        "电脑版数据",
        "电脑数据",
        "ecu数据",
        "标定数据",
        "程序数据",
        "原车数据",
        "刷写数据",
        "标定文件",
        "程序文件",
        "数据包",
        "bin文件",
    }
    META_FIND_GUIDE_KEYWORDS = {
        "怎么找",
        "如何找",
        "怎样找",
        "怎样才能找到",
        "怎么才能找到",
        "如何才能找到",
        "如何找到",
        "从哪里找",
        "去哪里找",
    }
    PARAM_QUERY_KEYWORDS = {
        "脚位",
        "哪个针脚",
        "几号脚",
        "接插件针脚",
        "接插件脚号",
        "插头针脚",
        "开路电压",
        "静态电压",
        "低怠速电压",
        "canh",
        "canl",
    }
    PIN_DOC_KEYWORDS = {
        "针脚定义",
        "引脚定义",
        "针脚图",
        "引脚图",
    }
    PARAM_EXACT_HINTS = {
        "哪个针脚",
        "几号脚",
        "什么作用",
        "什么意思",
        "定义是什么",
        "开路电压",
        "静态电压",
        "低怠速电压",
        "几伏",
        "电压",
        "接插件针脚",
        "接插件脚号",
        "插头针脚",
    }
    GENERAL_CHAT_KEYWORDS = {
        "怎么办",
        "怎么解决",
        "怎么处理",
        "怎么修",
        "如何解决",
        "如何处理",
        "什么原因",
        "为什么",
        "是什么",
        "什么是",
        "怎么回事",
        "原理",
        "工作原理",
        "区别",
        "多少",
        "多大",
        "正常吗",
        "正常是多少",
        "几伏",
        "几欧",
        "电阻",
    }

    def __init__(
        self,
        *,
        fault_code_parser: Any | None = None,
        diagnosis_enabled_provider: Any | None = None,
        config_service: Any | None = None,
        settings: Settings | None = None,
        model_override: Any | None = None,
        prompt_override: str | None = None,
        llm_observer: Any | None = None,
    ):
        self._fault_code_parser = fault_code_parser
        self._diagnosis_enabled_provider = diagnosis_enabled_provider or (lambda: False)
        self._config_service = config_service
        self._settings = settings or app_settings
        self._model_override = model_override
        self._prompt_override = prompt_override
        self._llm_observer = llm_observer
        self._agent = None
        self._agent_signature: tuple[Any, str, int, float, float] | None = None

    async def route_async(self, message: str, mode: str | None = None) -> IntentDecision:
        explicit = self._route_explicit_mode(message=message, mode=mode)
        if explicit is not None:
            return explicit

        text = (message or "").strip()
        if not text:
            return IntentDecision(intent=RoutedIntent.GENERAL_CHAT, reason="empty_message_default", source="default")

        parsed_fault_code = self._parse_fault_code(text)
        high_confidence = self._route_high_confidence_rule(text=text, parsed_fault_code=parsed_fault_code)
        if high_confidence is not None:
            return high_confidence

        llm_decision = await self._route_with_llm(text=text, parsed_fault_code=parsed_fault_code)
        if llm_decision is not None:
            return llm_decision

        return self._route_fallback(text=text, parsed_fault_code=parsed_fault_code)

    def route(self, message: str, mode: str | None = None) -> IntentDecision:
        explicit = self._route_explicit_mode(message=message, mode=mode)
        if explicit is not None:
            return explicit

        text = (message or "").strip()
        if not text:
            return IntentDecision(intent=RoutedIntent.GENERAL_CHAT, reason="empty_message_default", source="default")

        parsed_fault_code = self._parse_fault_code(text)
        high_confidence = self._route_high_confidence_rule(text=text, parsed_fault_code=parsed_fault_code)
        if high_confidence is not None:
            return high_confidence
        return self._route_fallback(text=text, parsed_fault_code=parsed_fault_code)

    def route_high_confidence(self, message: str) -> IntentDecision | None:
        text = (message or "").strip()
        if not text:
            return None
        return self._route_high_confidence_rule(
            text=text,
            parsed_fault_code=self._parse_fault_code(text),
        )

    def _route_explicit_mode(self, *, message: str, mode: str | None) -> IntentDecision | None:
        normalized_mode = (mode or "auto").strip().lower()
        text = (message or "").strip()
        if normalized_mode == "doc_search":
            return IntentDecision(intent=RoutedIntent.DOC_SEARCH, reason="explicit_mode_doc_search", source="explicit")
        if normalized_mode == "param_query":
            return IntentDecision(intent=RoutedIntent.PARAM_QUERY, reason="explicit_mode_param_query", source="explicit")
        if normalized_mode == "general_chat":
            return IntentDecision(intent=RoutedIntent.GENERAL_CHAT, reason="explicit_mode_general_chat", source="explicit")
        if normalized_mode == "fault_diagnosis":
            parsed = self._parse_fault_code(text)
            return IntentDecision(
                intent=self._diagnosis_intent(),
                reason=(
                    "explicit_mode_fault_diagnosis"
                    if self._diagnosis_enabled_provider()
                    else "explicit_mode_fault_diagnosis_service_disabled"
                ),
                source="explicit",
                normalized_fault_code=parsed,
                confidence=1.0,
            )
        return None

    async def _route_with_llm(self, *, text: str, parsed_fault_code: str | None) -> IntentDecision | None:
        if not bool(self._get_config("intent_router_enabled", self._settings.intent_router_enabled)):
            return None

        model = self._resolve_model()
        if not model or model == "test":
            return None

        prompt = self._build_prompt(text=text, parsed_fault_code=parsed_fault_code)
        system_prompt = self._resolve_system_prompt()
        max_tokens = int(self._get_config("intent_router_max_tokens", self._settings.intent_router_max_tokens))
        temperature = float(
            self._get_config("intent_router_temperature", self._settings.intent_router_temperature)
        )
        timeout = float(self._get_config("intent_router_timeout", self._settings.intent_router_timeout))

        try:
            agent = self._get_agent(
                model=model,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            llm_started_at = time.perf_counter()
            result = await agent.run(user_prompt=prompt)
            if self._llm_observer is not None:
                self._llm_observer(result, llm_started_at, "intent_router")
        except Exception as exc:
            logger.warning("intent routing llm failed, fallback to minimal rules. reason=%s", exc)
            return None

        output = result.output
        raw_intent = output.intent
        if raw_intent == RoutedIntent.FAULT_DIAGNOSIS.value:
            intent = self._diagnosis_intent()
        else:
            intent = RoutedIntent(raw_intent)

        return IntentDecision(
            intent=intent,
            reason=str(output.reason or "").strip() or "llm_intent_routing",
            source="llm",
            normalized_fault_code=parsed_fault_code,
            confidence=float(output.confidence),
        )

    def _resolve_model(self) -> Any:
        raw_model = self._model_override
        if raw_model is None:
            raw_model = (
                self._get_config("intent_router_model", None)
                or self._get_config("agent_model", self._settings.agent_model)
            )
        return normalize_configured_model(raw_model)

    def _resolve_system_prompt(self) -> str:
        prompt = self._prompt_override
        if prompt is None:
            prompt = self._get_config(
                "intent_router_system_prompt",
                self._settings.intent_router_system_prompt,
            )
        normalized = str(prompt or "").strip()
        return normalized or self._settings.intent_router_system_prompt

    def _build_prompt(self, *, text: str, parsed_fault_code: str | None) -> str:
        diagnosis_state = "enabled" if self._diagnosis_enabled_provider() else "disabled"
        fault_code_hint = parsed_fault_code or "未识别到明确故障码"
        return (
            f"用户原话：{text}\n"
            f"已识别故障码提示：{fault_code_hint}\n"
            f"外部故障码诊断服务：{diagnosis_state}\n"
            "请判断系统入口应先走哪个意图。"
        )

    def _get_agent(
        self,
        *,
        model: Any,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ):
        signature = (model, system_prompt, max_tokens, temperature, timeout)
        if self._agent is not None and self._agent_signature == signature:
            return self._agent

        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings

        self._agent = Agent(
            model=model,
            output_type=_IntentClassifierOutput,
            instructions=system_prompt,
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

    def _route_fallback(self, *, text: str, parsed_fault_code: str | None) -> IntentDecision:
        high_confidence = self._route_high_confidence_rule(text=text, parsed_fault_code=parsed_fault_code)
        if high_confidence is not None:
            return high_confidence

        if self._looks_like_pin_definition_doc_search(text):
            return IntentDecision(
                intent=RoutedIntent.DOC_SEARCH,
                reason="pin_definition_doc_material",
                source="fallback_rule",
                normalized_fault_code=parsed_fault_code,
            )

        if self._looks_like_ecu_data_doc_search(text):
            return IntentDecision(
                intent=RoutedIntent.DOC_SEARCH,
                reason="ecu_data_doc_material",
                source="fallback_rule",
                normalized_fault_code=parsed_fault_code,
            )

        if self._looks_like_doc_search(text):
            return IntentDecision(
                intent=RoutedIntent.DOC_SEARCH,
                reason="doc_material_keywords",
                source="fallback_rule",
                normalized_fault_code=parsed_fault_code,
            )

        if parsed_fault_code:
            return IntentDecision(
                intent=self._diagnosis_intent(),
                reason=(
                    "fault_code_detected"
                    if self._diagnosis_enabled_provider()
                    else "fault_code_detected_service_disabled"
                ),
                source="fallback_rule",
                normalized_fault_code=parsed_fault_code,
            )

        if self._looks_like_param_query(text):
            return IntentDecision(
                intent=RoutedIntent.PARAM_QUERY,
                reason="parameter_query_keywords",
                source="fallback_rule",
            )

        if self._looks_like_general_chat(text):
            return IntentDecision(
                intent=RoutedIntent.GENERAL_CHAT,
                reason="general_question_keywords",
                source="fallback_rule",
            )

        return IntentDecision(intent=RoutedIntent.GENERAL_CHAT, reason="default_general_chat", source="fallback_rule")

    def _route_high_confidence_rule(self, *, text: str, parsed_fault_code: str | None) -> IntentDecision | None:
        if self._looks_like_doc_body_search(text):
            return IntentDecision(
                intent=RoutedIntent.DOC_SEARCH,
                reason="doc_body_search_material",
                source="fallback_rule",
                normalized_fault_code=parsed_fault_code,
                confidence=0.98,
            )
        return None

    def _diagnosis_intent(self) -> RoutedIntent:
        if self._diagnosis_enabled_provider():
            return RoutedIntent.FAULT_DIAGNOSIS
        return RoutedIntent.FAULT_DIAGNOSIS_LLM

    def _looks_like_doc_search(self, text: str) -> bool:
        if any(keyword in text for keyword in self.META_FIND_GUIDE_KEYWORDS):
            return False

        if any(keyword in text for keyword in self.DOC_MATERIAL_KEYWORDS):
            return True

        has_action = any(keyword in text for keyword in self.DOC_ACTION_KEYWORDS)
        has_doc_target = any(keyword in text for keyword in self.DOC_TARGET_HINTS)
        return has_action and has_doc_target

    def _looks_like_doc_body_search(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        if any(keyword in normalized for keyword in self.META_FIND_GUIDE_KEYWORDS):
            return False
        if not any(keyword in normalized for keyword in self.DOC_MATERIAL_KEYWORDS):
            return False
        has_scope = any(keyword in normalized for keyword in self.DOC_BODY_SCOPE_HINTS)
        has_lookup = any(keyword in normalized for keyword in self.DOC_BODY_LOOKUP_HINTS)
        return has_scope and has_lookup

    def _looks_like_ecu_data_doc_search(self, text: str) -> bool:
        normalized = (text or "").strip()
        lowered = normalized.lower()
        if not normalized:
            return False

        if any(keyword in normalized for keyword in self.META_FIND_GUIDE_KEYWORDS):
            return False

        if any(keyword in lowered for keyword in self.ECU_DATA_MATERIAL_KEYWORDS):
            return any(keyword in normalized for keyword in self.DOC_ACTION_KEYWORDS) or not any(
                keyword in normalized for keyword in self.META_FIND_GUIDE_KEYWORDS
            )

        has_data_target = (
            ("电脑版" in normalized or "电脑板" in normalized or "ecu" in lowered)
            and any(token in normalized for token in ("数据", "标定", "程序", "文件"))
        )
        if not has_data_target:
            return False

        if any(keyword in normalized for keyword in self.META_FIND_GUIDE_KEYWORDS):
            return False

        return bool(
            re.search(r"\b(?:EDC|SID|DCV|CM|ME|CV|MD)[A-Z0-9\-]{2,}\b", normalized, re.IGNORECASE)
            or "发动机" in normalized
            or "电脑版" in normalized
            or "电脑板" in normalized
        )

    def _looks_like_general_chat(self, text: str) -> bool:
        if any(keyword in text for keyword in self.META_FIND_GUIDE_KEYWORDS):
            return True

        if any(keyword in text for keyword in self.GENERAL_CHAT_KEYWORDS):
            return True

        return "?" in text or "？" in text

    def _looks_like_param_query(self, text: str) -> bool:
        lowered = text.lower()
        if any(keyword in text for keyword in self.PIN_DOC_KEYWORDS):
            if self._extract_pin_token(text):
                return True
            if any(keyword in text for keyword in self.PARAM_EXACT_HINTS):
                return True
            if "canh" in lowered or "canl" in lowered:
                return True
            return False

        if any(keyword in lowered for keyword in self.PARAM_QUERY_KEYWORDS):
            return True

        pin_like = bool(self._extract_pin_token(text))
        if pin_like and any(keyword in text for keyword in ("作用", "定义", "电压", "几伏", "信号")):
            return True

        return False

    def _looks_like_pin_definition_doc_search(self, text: str) -> bool:
        lowered = text.lower()
        if not any(keyword in text for keyword in self.PIN_DOC_KEYWORDS):
            return False
        if self._extract_pin_token(text):
            return False
        if any(keyword in text for keyword in self.PARAM_EXACT_HINTS):
            return False
        if "canh" in lowered or "canl" in lowered:
            return False
        return True

    @staticmethod
    def _extract_pin_token(text: str) -> str | None:
        matched = re.search(r"\b([A-Za-z]{1,4}\s*[-]?\s*\d{1,3})\b", text)
        if matched is None:
            return None
        return matched.group(1)

    def _parse_fault_code(self, text: str) -> str | None:
        if self._fault_code_parser is None:
            return None

        parsed = self._fault_code_parser.parse_first(text)
        if parsed is None:
            return None
        return parsed.normalized
