from __future__ import annotations

import abc
import enum
import json
import os
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union
from urllib import request as urllib_request

from .types import UserProfile, resolve_known_items, resolve_uncertain_items


FALLBACK_OPTION_MARKERS = (
    "其他",
    "其它",
    "不确定",
    "不知道",
    "都不是",
    "以上都不是",
    "无法确认",
    "无合适",
    "不清楚",
)

PERSONA_STYLES = {"normal", "cooperative_vague", "term_confused", "image_parsing_required"}
CORRECTION_STYLES = {"immediate", "delayed"}
TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 180.0
DEFAULT_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_TIMEOUT_SECONDS = 600.0
DEFAULT_OPENROUTER_RETRY_ATTEMPTS = 4
DEFAULT_OPENROUTER_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_USER_MODEL = "openrouter:deepseek/deepseek-chat-v3-0324"
_WARMED_USER_MODELS: set[tuple[str, str | None, str | None, float | None]] = set()
STOP_REASON_CODES = {
    "OPTION_SPACE_CONFLICT",
    "INSUFFICIENT_INFORMATION",
}
_LITELLM_PROVIDER_PREFIXES = {
    "openrouter",
    "google-gla",
    "openai",
    "anthropic",
    "deepseek",
    "groq",
    "xai",
    "ollama",
}
_RETRYABLE_OPENROUTER_ERROR_MARKERS = (
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
    "openrouterexception",
    "apiconnectionerror",
    "apitimeouterror",
    "remoteprotocolerror",
    "server disconnected without sending a response",
    "connection reset",
    "connection aborted",
    "connection dropped",
    "read timeout",
    "timed out",
    "tlsv1 alert",
    "ssl",
    "handshake",
)


@dataclass(frozen=True)
class _CompatMessage:
    role: str
    content: str | None

    def model_dump(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class _CompatChoice:
    message: _CompatMessage


@dataclass(frozen=True)
class _CompatResponse:
    choices: list[_CompatChoice]
    _hidden_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class _CompletionClientBundle:
    client: Any
    close: Callable[[], None]


class UserSimulationProviderError(RuntimeError):
    """Raised when the simulated-user model call itself fails."""


def _env_float(*keys: str, default: float) -> float:
    for key in keys:
        raw_value = str(os.environ.get(key) or "").strip()
        if not raw_value:
            continue
        try:
            return float(raw_value)
        except ValueError:
            continue
    return default


def _env_int(*keys: str, default: int) -> int:
    for key in keys:
        raw_value = str(os.environ.get(key) or "").strip()
        if not raw_value:
            continue
        try:
            return int(raw_value)
        except ValueError:
            continue
    return default


def _is_openrouter_target(*, model: str, provider: Optional[str]) -> bool:
    return provider is None and model.startswith("openrouter/")


def _completion_kwargs(model: str, provider: Optional[str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if provider == "ollama":
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        base_url = (
            os.environ.get("BENCHMARK_OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or os.environ.get("OLLAMA_API_BASE")
            or DEFAULT_OLLAMA_BASE_URL
        )
        timeout_seconds = _env_float(
            "BENCHMARK_OLLAMA_TIMEOUT_SECONDS",
            "BENCHMARK_USER_TIMEOUT_SECONDS",
            default=DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        )
        kwargs["base_url"] = base_url
        kwargs["timeout"] = timeout_seconds
        return kwargs

    if _is_openrouter_target(model=model, provider=provider):
        kwargs["timeout"] = _env_float(
            "BENCHMARK_OPENROUTER_TIMEOUT_SECONDS",
            "BENCHMARK_USER_TIMEOUT_SECONDS",
            default=DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
        )
    return kwargs


def _resolve_completion_target(model: str, provider: Optional[str]) -> tuple[str, Optional[str]]:
    normalized_model = str(model or "").strip()
    normalized_provider = str(provider or "").strip() or None
    if not normalized_model:
        return normalized_model, normalized_provider

    if normalized_provider:
        if normalized_provider == "openrouter":
            if normalized_model.startswith("openrouter/"):
                return normalized_model, None
            prefix = "openrouter:"
            if normalized_model.startswith(prefix):
                stripped_model = normalized_model[len(prefix) :].strip()
                if stripped_model:
                    return f"openrouter/{stripped_model}", None
            return f"openrouter/{normalized_model}", None
        prefix = f"{normalized_provider}:"
        if normalized_model.startswith(prefix):
            stripped_model = normalized_model[len(prefix) :].strip()
            return stripped_model or normalized_model, normalized_provider
        return normalized_model, normalized_provider

    if normalized_model.startswith("openrouter/"):
        return normalized_model, None

    if ":" not in normalized_model:
        return normalized_model, None

    candidate_provider, _, stripped_model = normalized_model.partition(":")
    candidate_provider = candidate_provider.strip()
    stripped_model = stripped_model.strip()
    if candidate_provider == "openrouter" and stripped_model:
        return f"openrouter/{stripped_model}", None
    if candidate_provider in _LITELLM_PROVIDER_PREFIXES and stripped_model:
        return stripped_model, candidate_provider
    return normalized_model, None


def _ollama_chat_completion(*, model: str, messages: list[dict[str, Any]]) -> _CompatResponse:
    kwargs = _completion_kwargs(model=model, provider="ollama")
    base_url = str(kwargs["base_url"]).rstrip("/")
    timeout_seconds = float(kwargs["timeout"])
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": 256},
    }
    req = urllib_request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    message = raw.get("message") or {}
    content = message.get("content")
    role = str(message.get("role") or "assistant")
    return _CompatResponse(choices=[_CompatChoice(message=_CompatMessage(role=role, content=content))])


def _should_use_fresh_openai_client(*, model: str, provider: Optional[str]) -> bool:
    if os.environ.get("BENCHMARK_USER_OPENAI_COMPAT_FRESH_CLIENT", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    return _is_openrouter_target(model=model, provider=provider)


def _openrouter_retry_attempts() -> int:
    return max(
        1,
        _env_int(
            "BENCHMARK_OPENROUTER_RETRY_ATTEMPTS",
            "BENCHMARK_USER_RETRY_ATTEMPTS",
            default=DEFAULT_OPENROUTER_RETRY_ATTEMPTS,
        ),
    )


def _openrouter_retry_sleep_seconds(attempt_index: int) -> float:
    base_seconds = _env_float(
        "BENCHMARK_OPENROUTER_RETRY_BACKOFF_SECONDS",
        "BENCHMARK_USER_RETRY_BACKOFF_SECONDS",
        default=DEFAULT_OPENROUTER_RETRY_BACKOFF_SECONDS,
    )
    return max(0.0, base_seconds * max(1, attempt_index))


def _is_retryable_openrouter_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in _RETRYABLE_OPENROUTER_ERROR_MARKERS)


def _build_openrouter_completion_client() -> _CompletionClientBundle:
    try:
        import httpx
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("OpenRouter 用户模拟需要安装 openai 与 httpx。") from exc

    api_key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("CRS_OPENROUTER_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("OpenRouter 用户模拟缺少 OPENROUTER_API_KEY。")

    api_base = (
        os.environ.get("OPENROUTER_BASE_URL")
        or os.environ.get("CRS_OPENROUTER_BASE_URL")
        or DEFAULT_OPENROUTER_API_BASE
    ).strip() or DEFAULT_OPENROUTER_API_BASE

    timeout_seconds = _env_float(
        "BENCHMARK_OPENROUTER_TIMEOUT_SECONDS",
        "BENCHMARK_USER_TIMEOUT_SECONDS",
        default=DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
    )
    transport_timeout = httpx.Timeout(
        timeout=timeout_seconds,
        connect=min(30.0, timeout_seconds),
        read=timeout_seconds,
        write=min(30.0, timeout_seconds),
        pool=min(30.0, timeout_seconds),
    )
    http_client = httpx.Client(
        trust_env=True,
        follow_redirects=True,
        timeout=transport_timeout,
    )
    openai_client = OpenAI(
        api_key=api_key,
        base_url=api_base,
        http_client=http_client,
        max_retries=0,
    )

    def _close() -> None:
        with suppress(Exception):
            openai_client.close()
        with suppress(Exception):
            http_client.close()

    return _CompletionClientBundle(client=openai_client, close=_close)


def _build_completion_client(*, model: str, provider: Optional[str]) -> _CompletionClientBundle | None:
    if _should_use_fresh_openai_client(model=model, provider=provider):
        return _build_openrouter_completion_client()
    return None


def _completion(
    *,
    model: str,
    provider: Optional[str],
    messages: list[dict[str, Any]],
    extra_kwargs: Optional[dict[str, Any]] = None,
) -> Any:
    resolved_model, resolved_provider = _resolve_completion_target(model=model, provider=provider)
    if resolved_provider == "ollama":
        return _ollama_chat_completion(model=resolved_model, messages=messages)
    try:
        from litellm import completion
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("LLM 用户模拟需要安装 litellm。") from exc
    retry_attempts = 1
    if _is_openrouter_target(model=resolved_model, provider=resolved_provider):
        retry_attempts = _openrouter_retry_attempts()

    last_error: Exception | None = None
    for provider_attempt in range(1, retry_attempts + 1):
        client_bundle = _build_completion_client(model=resolved_model, provider=resolved_provider)
        completion_kwargs = _completion_kwargs(model=resolved_model, provider=resolved_provider)
        if extra_kwargs:
            completion_kwargs.update(dict(extra_kwargs))
        if client_bundle is not None:
            completion_kwargs["client"] = client_bundle.client
        try:
            return completion(
                model=resolved_model,
                custom_llm_provider=resolved_provider,
                messages=messages,
                **completion_kwargs,
            )
        except Exception as exc:
            last_error = exc
            if (
                not _is_openrouter_target(model=resolved_model, provider=resolved_provider)
                or provider_attempt >= retry_attempts
                or not _is_retryable_openrouter_error(exc)
            ):
                break
            time.sleep(_openrouter_retry_sleep_seconds(provider_attempt))
        finally:
            if client_bundle is not None:
                client_bundle.close()

    assert last_error is not None
    if _is_openrouter_target(model=resolved_model, provider=resolved_provider) and retry_attempts > 1:
        raise UserSimulationProviderError(
            f"OpenRouter 用户模拟调用失败；已执行 {retry_attempts} 次传输重试；最后错误: {last_error}"
        ) from last_error
    raise UserSimulationProviderError(str(last_error)) from last_error


def _structured_decision_completion_kwargs(
    *,
    model: str,
    provider: Optional[str],
) -> dict[str, Any]:
    resolved_model, resolved_provider = _resolve_completion_target(model=model, provider=provider)
    if resolved_provider == "ollama":
        return {}
    if resolved_model.startswith("openrouter/") or resolved_provider in {"openai", "deepseek"}:
        return {
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
    return {}


def warmup_user_model(model: Optional[str], provider: Optional[str]) -> None:
    if not model or provider != "ollama":
        return

    kwargs = _completion_kwargs(model=model, provider=provider)
    cache_key = (
        model,
        provider,
        str(kwargs.get("base_url")) if kwargs.get("base_url") is not None else None,
        float(kwargs["timeout"]) if kwargs.get("timeout") is not None else None,
    )
    if cache_key in _WARMED_USER_MODELS:
        return

    _ollama_chat_completion(
        model=model,
        messages=[{"role": "user", "content": "请只回复ok"}],
    )
    _WARMED_USER_MODELS.add(cache_key)


@dataclass(frozen=True)
class AskUserOption:
    key: str
    label: str
    description: str | None = None


@dataclass(frozen=True)
class AskUserDecisionContext:
    ask_user_question: str
    options: list[AskUserOption] = field(default_factory=list)
    conversation_turn_count: int = 0
    scenario: str = "normal"
    initial_user_message: str | None = None
    user_profile: UserProfile | None = None
    ask_user_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuredUserDecision:
    decision_kind: str
    user_message: str | None = None
    selected_option_key: str | None = None
    selected_option_label: str | None = None
    rollback_target_round: int | None = None
    stop_reason_code: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


StructuredDecisionTraceHook = Callable[[str, dict[str, Any]], None]


def parse_structured_user_decision(raw_value: str | dict[str, Any]) -> StructuredUserDecision:
    if isinstance(raw_value, str):
        payload = json.loads(raw_value)
    elif isinstance(raw_value, dict):
        payload = raw_value
    else:
        raise ValueError("structured user decision must be a json object or json string")

    decision_kind = str(payload.get("decision_kind") or "").strip()
    if decision_kind not in {"initial_message", "choose_option", "declare_rollback_intent", "stop"}:
        raise ValueError(f"unsupported decision_kind: {decision_kind}")

    rollback_target_round = payload.get("rollback_target_round")
    if rollback_target_round is not None:
        try:
            rollback_target_round = int(rollback_target_round)
        except (TypeError, ValueError) as exc:
            raise ValueError("rollback_target_round must be an integer") from exc

    def _optional_text(name: str) -> str | None:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    evidence = payload.get("evidence")
    if evidence is None:
        normalized_evidence: dict[str, Any] = {}
    elif isinstance(evidence, dict):
        normalized_evidence = dict(evidence)
    else:
        raise ValueError("evidence must be a json object when provided")

    stop_reason_code = _optional_text("stop_reason_code")
    if decision_kind == "stop":
        if stop_reason_code not in STOP_REASON_CODES:
            allowed_codes = ", ".join(sorted(STOP_REASON_CODES))
            raise ValueError(f"stop must provide stop_reason_code in {{{allowed_codes}}}")
    elif stop_reason_code is not None:
        raise ValueError("only stop decision_kind can provide stop_reason_code")

    return StructuredUserDecision(
        decision_kind=decision_kind,
        user_message=_optional_text("user_message"),
        selected_option_key=_optional_text("selected_option_key"),
        selected_option_label=_optional_text("selected_option_label"),
        rollback_target_round=rollback_target_round,
        stop_reason_code=stop_reason_code,
        evidence=normalized_evidence,
        reason=_optional_text("reason"),
    )


def _extract_json_object_text(raw_text: str) -> str:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("structured user decision must contain one json object")
    return stripped[start : end + 1]


def build_structured_decision_prompt(
    *,
    instruction: str,
    transcript: str,
    context: AskUserDecisionContext,
) -> str:
    option_lines = []
    for index, option in enumerate(context.options, start=1):
        suffix = f"；说明：{option.description}" if option.description else ""
        option_lines.append(f"{index}. key={option.key}；label={option.label}{suffix}")
    options_block = "\n".join(option_lines) if option_lines else "当前没有可选项。"

    return f"""你正在扮演一个 benchmark 里的 AI 模拟用户。

任务指令：
{instruction}

当前交互轨迹：
{transcript}

当前 ask_user 问题：
{context.ask_user_question}

当前场景：
{context.scenario}

当前轮次：
{context.conversation_turn_count}

当前可选项：
{options_block}

你必须只输出一个 JSON 对象，字段如下：
{{
  "decision_kind": "initial_message | choose_option | declare_rollback_intent | stop",
  "user_message": "首轮自由文本时使用，其他情况可为空",
  "selected_option_key": "选项key，可为空",
  "selected_option_label": "选项label，可为空",
  "rollback_target_round": 1,
  "stop_reason_code": "OPTION_SPACE_CONFLICT | INSUFFICIENT_INFORMATION",
  "evidence": {{
    "supports": ["命中的线索"],
    "conflicts": ["冲突点"]
  }},
  "reason": "一句短理由"
}}

规则：
- 如果选择选项，必须从当前真实可选项里挑，不能编造不存在的 key 或 label。
- 只能依据用户当前明确知道的信息做选择，不能补充 case 中没有提供的事实。
- 如果你想撤回，只能输出撤回意图，不能伪造系统已经撤回成功。
- 如果要 stop，必须附带合法的 stop_reason_code。
- `reason` 尽量使用简短中文。
- `evidence.supports` 和 `evidence.conflicts` 尽量填写中文短语。
- 只有 `stop_reason_code` 保持固定英文枚举值，其余说明性文本尽量不要写英文。
- 除 JSON 外不要输出任何别的文字。"""


def _safe_text(value: str | None) -> str:
    return str(value or "").strip()


def _resolved_known_items(profile: UserProfile | None) -> list[str]:
    return [item for item in resolve_known_items(profile) if _safe_text(item)]


def _resolved_uncertain_items(profile: UserProfile | None) -> list[str]:
    return [item for item in resolve_uncertain_items(profile) if _safe_text(item)]


def _normalized_persona(context: AskUserDecisionContext) -> str:
    candidates = [
        _safe_text(getattr(context.user_profile, "persona", None)).lower(),
        _safe_text(context.scenario).lower(),
    ]
    for candidate in candidates:
        if candidate in PERSONA_STYLES:
            return candidate
    return "normal"


def _normalized_correction_style(context: AskUserDecisionContext, persona: str) -> str:
    raw_style = _safe_text(getattr(context.user_profile, "correction_style", None)).lower()
    if raw_style in CORRECTION_STYLES:
        return raw_style
    return "immediate" if persona == "normal" else "delayed"


def build_persona_structured_decision_prompt(
    *,
    instruction: str,
    transcript: str,
    context: AskUserDecisionContext,
) -> str:
    option_lines = []
    for index, option in enumerate(context.options, start=1):
        suffix = f"；说明：{option.description}" if option.description else ""
        option_lines.append(
            f"{index}. key={option.key}；label={option.label}{suffix}"
        )
    options_block = "\n".join(option_lines) if option_lines else "当前没有候选项。"
    known_items = _resolved_known_items(context.user_profile)
    uncertain_items = _resolved_uncertain_items(context.user_profile)
    known_items_block = "、".join(known_items) if known_items else "无"
    uncertain_items_block = "、".join(uncertain_items) if uncertain_items else "无"
    goal_text = _safe_text(getattr(context.user_profile, "goal", None)) or "无"
    initial_user_message = _safe_text(context.initial_user_message) or "无"
    aliases_block = json.dumps(
        dict(getattr(context.user_profile, "aliases", {}) or {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    notes_text = _safe_text(getattr(context.user_profile, "notes", None)) or "无"
    ask_user_context_block = json.dumps(dict(context.ask_user_context or {}), ensure_ascii=False, sort_keys=True)

    return f"""你正在扮演一个 benchmark 里的 AI 模拟用户。

任务指令：
{instruction}

当前交互轨迹：
{transcript}

当前 ask_user 问题：
{context.ask_user_question}

当前用户人格：
- persona：{_normalized_persona(context)}
- correction_style：{_normalized_correction_style(context, _normalized_persona(context))}
- goal：{goal_text}
- initial_user_message：{initial_user_message}

当前你明确知道的线索：
- known_items：{known_items_block}
- uncertain_items：{uncertain_items_block}
- aliases：{aliases_block}
- notes：{notes_text}

当前 ask_user.context：
{ask_user_context_block}

当前可选项：
{options_block}

你必须只输出一个 JSON 对象，字段如下：
{{
  "decision_kind": "choose_option | stop | declare_rollback_intent",
  "selected_option_key": "真实选项 key，可为空",
  "selected_option_label": "真实选项 label，可为空",
  "rollback_target_round": 1,
  "stop_reason_code": "OPTION_SPACE_CONFLICT | INSUFFICIENT_INFORMATION",
  "evidence": {{
    "supports": ["命中的已知线索"],
    "conflicts": ["冲突点"]
  }},
  "reason": "一句短理由"
}}

规则：
- 只能从当前真实可选项中做最后决策，不能编造不存在的 key 或 label。
- 只能依据当前对话、当前问题、当前选项、ask_user.context，以及用户画像里明确给出的信息做选择，不能补充 case 中没有提供的事实。
- 如果某个具体选项与已知信息最匹配，就选择该选项。
- 允许做合理推断，但在输出前先自检：`evidence.supports` 里的支撑线索必须能回指到当前对话、`known_items`、`uncertain_items`、`aliases`、`ask_user.context` 或当前上下文里已经明确出现的内容。
- 只有当推断链最终能落回至少一个较具体、能区分候选项的线索时，才允许选择具体项；不要把“看起来相关”当成“已知支持”。
- `ECU`、`发动机`、`电路图`、`控制器`、`板子`、`针脚` 这类泛词、部件大类或行业通用词，单独出现时不能直接支持某个具体品牌、型号、厂商或针数选项。
- 如果前面的具体枚举项都不准确，但 `其他/不确定/不清楚/无法确认/以上都不是` 这类兜底选项可以真实表达你的状态，优先选择该兜底项，不要滥用 stop。
- 如果用户确实不知道当前问题要求确认的信息，而且当前没有任何可表达“不确定/其他”的兜底选项，才允许 stop。
- 如果当前选项空间与你已知信息明显不相容，并且 `其他/不确定` 也无法准确表达，才允许 stop。
- 输出 `stop` 时必须填写合法的 `stop_reason_code`，并在 `evidence` 里给出 supports/conflicts。
- `reason` 尽量使用简短中文。
- `evidence.supports` 和 `evidence.conflicts` 尽量填写中文短语。
- 只有 `stop_reason_code` 保持固定英文枚举值，其余说明性文本尽量不要写英文。
- 不要为了让流程继续而强行选择一个不符合用户认知的选项。
- 若当前场景是 `image_parsing_required`，用户视角不能从图片中得到任何新信息，只能依据文字已知信息做决定。
- 若当前场景不是 `image_parsing_required`，只有当前上下文里已经明确给出的、图片中清晰可读且稳定的线索，才可以视作用户已知信息；不要脑补模糊图片内容。
- 如果要表达撤回，只能输出 `declare_rollback_intent`，不能伪造已经撤回成功。
- 除 JSON 外不要输出任何别的文字。"""


def _find_candidate_option(
    options: list[AskUserOption],
    *,
    selected_option_key: str | None,
    selected_option_label: str | None,
) -> AskUserOption | None:
    normalized_key = _safe_text(selected_option_key)
    normalized_label = _safe_text(selected_option_label)
    for option in options:
        if normalized_key and option.key == normalized_key:
            return option
    for option in options:
        if normalized_label and option.label == normalized_label:
            return option
    return None


def _validate_persona_decision(
    decision: StructuredUserDecision,
    context: AskUserDecisionContext,
) -> str | None:
    if decision.decision_kind == "declare_rollback_intent":
        if decision.rollback_target_round is None or decision.rollback_target_round < 1:
            return "declare_rollback_intent 必须提供正整数 rollback_target_round。"
        return None
    if decision.decision_kind == "stop":
        if decision.stop_reason_code not in STOP_REASON_CODES:
            return "stop 必须提供合法的 stop_reason_code。"
        return None
    if decision.decision_kind != "choose_option":
        return "当前 ask_user 轮只允许 choose_option、stop 或 declare_rollback_intent。"
    if _find_candidate_option(
        context.options,
        selected_option_key=decision.selected_option_key,
        selected_option_label=decision.selected_option_label,
    ) is None:
        return "choose_option 必须从当前真实选项中选择真实存在的选项。"
    return None


def _finalize_persona_decision(
    decision: StructuredUserDecision,
    context: AskUserDecisionContext,
) -> StructuredUserDecision:
    if decision.decision_kind == "declare_rollback_intent":
        return StructuredUserDecision(
            decision_kind=decision.decision_kind,
            rollback_target_round=decision.rollback_target_round,
            evidence=dict(decision.evidence),
            reason=decision.reason,
        )

    if decision.decision_kind == "stop":
        return StructuredUserDecision(
            decision_kind="stop",
            stop_reason_code=decision.stop_reason_code,
            evidence=dict(decision.evidence),
            reason=decision.reason,
        )

    selected_option = _find_candidate_option(
        context.options,
        selected_option_key=decision.selected_option_key,
        selected_option_label=decision.selected_option_label,
    )
    if selected_option is None:
        raise ValueError("choose_option 必须从当前真实选项中选择真实存在的选项。")

    return StructuredUserDecision(
        decision_kind="choose_option",
        selected_option_key=_safe_text(selected_option.key) or None,
        selected_option_label=_safe_text(selected_option.label) or None,
        evidence=dict(decision.evidence),
        reason=decision.reason,
    )


def _build_persona_retry_message(
    *,
    validation_error: str,
) -> str:
    return (
        f"你上一条输出不合格，原因：{validation_error}\n"
        "请重新输出一个 JSON 对象。\n"
        "如果选择选项，只能填写当前真实选项中的真实 key 或 label；若选择具体项，`evidence.supports` 必须给出能区分候选项的具体线索，不要只写泛词。"
    )


def generate_persona_user_decision(
    *,
    user_strategy: Union[str, UserStrategy],
    model: Optional[str],
    provider: Optional[str],
    instruction: str,
    transcript: str,
    context: AskUserDecisionContext,
    max_attempts: int = 2,
    trace_hook: StructuredDecisionTraceHook | None = None,
) -> StructuredUserDecision:
    if model is None:
        raise ValueError("AI 用户结构化决策需要配置 model")

    strategy = get_user_strategy(user_strategy)
    attempt_limit = max(1, int(max_attempts))
    if strategy == UserStrategy.VERIFY:
        attempt_limit = max(attempt_limit, 3)
    if strategy in {UserStrategy.REACT, UserStrategy.REFLECTION}:
        attempt_limit = max(attempt_limit, 4)

    prompt = build_persona_structured_decision_prompt(
        instruction=instruction,
        transcript=transcript,
        context=context,
    )
    base_messages = [
        {
            "role": "system",
            "content": "你是 benchmark 中的 AI 模拟用户。你只能输出一个 JSON 对象，不要输出额外文字。",
        },
        {"role": "user", "content": prompt},
    ]
    messages = list(base_messages)
    last_error: Exception | None = None

    for attempt_index in range(1, attempt_limit + 1):
        if trace_hook is not None:
            trace_hook(
                "用户模拟模型调用",
                {
                    "internal_attempt": attempt_index,
                    "attempt_limit": attempt_limit,
                    "strategy": strategy.value,
                    "model": model,
                    "provider": provider,
                },
            )
        res = _completion(
            model=model,
            provider=provider,
            messages=messages,
            extra_kwargs=_structured_decision_completion_kwargs(model=model, provider=provider),
        )
        raw_text = str(res.choices[0].message.content or "").strip()
        try:
            decision = parse_structured_user_decision(_extract_json_object_text(raw_text))
        except Exception as exc:
            last_error = exc
            if trace_hook is not None:
                trace_hook(
                    "用户模拟输出非法",
                    {
                        "internal_attempt": attempt_index,
                        "attempt_limit": attempt_limit,
                        "strategy": strategy.value,
                        "model": model,
                        "provider": provider,
                        "error": str(exc),
                        "raw_text": raw_text,
                    },
                )
            messages = [
                *base_messages,
                {"role": "assistant", "content": raw_text},
                {
                    "role": "user",
                    "content": "你上一条输出不是合法 JSON。请严格只输出一个 JSON 对象。",
                },
            ]
            continue

        validation_error = _validate_persona_decision(decision, context)
        if validation_error is None:
            return _finalize_persona_decision(decision, context)

        last_error = ValueError(validation_error)
        if trace_hook is not None:
            trace_hook(
                "用户模拟校验失败",
                {
                "internal_attempt": attempt_index,
                    "attempt_limit": attempt_limit,
                    "strategy": strategy.value,
                    "model": model,
                    "provider": provider,
                    "error": validation_error,
                    "raw_text": raw_text,
                },
            )
        messages = [
            *base_messages,
            {"role": "assistant", "content": raw_text},
            {
                "role": "user",
                "content": _build_persona_retry_message(
                    validation_error=validation_error,
                ),
            },
        ]

    assert last_error is not None
    raise ValueError(f"persona structured decision invalid: {last_error}") from last_error


class BaseUserSimulationEnv(abc.ABC):
    metadata: dict[str, Any] = {}

    @abc.abstractmethod
    def reset(self, instruction: Optional[str] = None) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def step(self, content: str) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def get_total_cost(self) -> float:
        raise NotImplementedError


class HumanUserSimulationEnv(BaseUserSimulationEnv):
    def reset(self, instruction: Optional[str] = None) -> str:
        if instruction:
            print(instruction)
        return input("助手：您好，请问您想找什么资料？\n用户：")

    def step(self, content: str) -> str:
        return input(f"助手：{content}\n用户：")

    def get_total_cost(self) -> float:
        return 0.0


class LLMUserSimulationEnv(BaseUserSimulationEnv):
    def __init__(self, model: str, provider: Optional[str]) -> None:
        super().__init__()
        self.messages: list[dict[str, Any]] = []
        self.model = model
        self.provider = provider
        self.total_cost = 0.0
        self.reset()

    def build_system_prompt(self, instruction: Optional[str]) -> str:
        instruction_display = f"\n\n任务指令：\n{instruction}\n" if instruction is not None else ""
        return f"""你正在扮演一个用户，与一个 CRS 资料检索助手进行对话。{instruction_display}
规则：
- 每次只生成一轮“用户消息”，不要一次性说完全部信息。
- 你的主要目标是帮助助手最终定位到正确的资料文件。
- 当前 benchmark 阶段页码功能还没有正式实现，所以不要无端强迫助手必须给出页码；如果助手已经基本定位到正确文件，你可以结束对话。
- 如果助手询问的信息不在任务指令中，例如你并不知道的订单号、文件编号、OCR 细节、图片里的额外文字，就直接说你不知道、记不清、或者只能提供当前这些内容，不要编造。
- 不要机械复述任务指令，要自然地表达。
- 如果任务目标已经满足，请单独输出 ###STOP###，不要带任何其它文字。
- 默认使用中文、自然、简短的口语表达。"""

    def generate_next_message(self, messages: list[dict[str, Any]]) -> str:
        res = _completion(model=self.model, provider=self.provider, messages=messages)
        message = res.choices[0].message
        self.messages.append(message.model_dump())
        self.total_cost += res._hidden_params.get("response_cost") or 0.0
        return str(message.content or "").strip()

    def reset(self, instruction: Optional[str] = None) -> str:
        self.messages = [
            {"role": "system", "content": self.build_system_prompt(instruction=instruction)},
            {"role": "user", "content": "您好，请问您想找什么资料？"},
        ]
        return self.generate_next_message(self.messages)

    def step(self, content: str) -> str:
        self.messages.append({"role": "user", "content": content})
        return self.generate_next_message(self.messages)

    def get_total_cost(self) -> float:
        return self.total_cost


class ReactUserSimulationEnv(LLMUserSimulationEnv):
    def build_system_prompt(self, instruction: Optional[str]) -> str:
        instruction_display = f"\n\n任务指令：\n{instruction}\n" if instruction is not None else ""
        return f"""你正在扮演一个用户，与一个 CRS 资料检索助手进行对话。{instruction_display}
规则：
- 先生成一段 Thought，说明你下一轮准备怎么回应，这一段不会发给助手。
- 再生成一段 User Response，作为真正发给助手的话。
- 不要一次性说完全部信息，只在必要时逐步补充。
- 不要编造任务指令中没有提供的 OCR、文件编号、车型细节、页码等信息。
- 当前 benchmark 阶段页码功能还没有正式实现，所以不要无端强迫助手必须给出页码；如果助手已经基本定位到正确文件，你可以结束对话。
- 如果目标已经满足，请把 User Response 单独写成 ###STOP###。
- 默认使用中文、自然、简短的表达。

输出格式：
Thought:
<你的思考>

User Response:
<真正发给助手的一句话>"""

    def parse_response(self, response: str) -> str:
        if "###STOP###" in response:
            return "###STOP###"
        if "User Response:" in response:
            _, user_response = response.split("User Response:", maxsplit=1)
            return user_response.strip()
        return response.strip()

    def generate_next_message(self, messages: list[dict[str, Any]]) -> str:
        res = _completion(model=self.model, provider=self.provider, messages=messages)
        message = res.choices[0].message
        self.messages.append(message.model_dump())
        self.total_cost += res._hidden_params.get("response_cost") or 0.0
        return self.parse_response(str(message.content or ""))


def _map_role_label(role: str) -> str:
    if role == "user":
        return "助手"
    if role == "assistant":
        return "用户模拟器"
    return role


def verify(model: str, provider: Optional[str], response: str, messages: list[dict[str, Any]]) -> bool:
    transcript = "\n".join(
        [f"{_map_role_label(message['role'])}: {message['content']}" for message in messages]
    )
    prompt = f"""你是一个多轮 benchmark 的监督者。下面给你一段“助手”和“模拟用户”的对话历史，以及模拟用户新生成的一轮回复。
请判断这轮回复是否合格。

判断标准：
- 回复是否符合任务指令
- 是否没有编造未知信息
- 是否没有一次性泄露过多信息
- 是否保持自然中文对话
- 若目标已满足，是否正确使用 ###STOP###

你只能回答 true 或 false。

对话历史：
{transcript}

候选回复：
{response}

Classification:"""
    res = _completion(
        model=model,
        provider=provider,
        messages=[{"role": "user", "content": prompt}],
    )
    return "true" in str(res.choices[0].message.content or "").lower()


def reflect(model: str, provider: Optional[str], response: str, messages: list[dict[str, Any]]) -> str:
    transcript = "\n".join(
        [f"{_map_role_label(message['role'])}: {message['content']}" for message in messages]
    )
    prompt = f"""你是一个多轮 benchmark 的监督者。下面给你一段“助手”和“模拟用户”的对话历史，以及一个不合格的模拟用户回复。
请先反思问题，再给出一个更好的新回复。

要求：
- 不要编造未知信息
- 不要一次性说完所有内容
- 保持自然中文
- 若目标已满足，允许输出 ###STOP###

格式：
Reflection:
<反思>

Response:
<新的用户回复>

对话历史：
{transcript}

不合格回复：
{response}"""
    res = _completion(
        model=model,
        provider=provider,
        messages=[{"role": "user", "content": prompt}],
    )
    content = str(res.choices[0].message.content or "")
    if "Response:" in content:
        _, new_response = content.split("Response:", maxsplit=1)
        return new_response.strip()
    return content.strip()


class VerifyUserSimulationEnv(LLMUserSimulationEnv):
    def __init__(self, model: str, provider: Optional[str], max_attempts: int = 3) -> None:
        self.model = model
        self.provider = provider
        self.max_attempts = max_attempts
        self.total_cost = 0.0
        self.messages = []
        self.reset()

    def generate_next_message(self, messages: list[dict[str, Any]]) -> str:
        attempts = 0
        cur_message = None
        while attempts < self.max_attempts:
            res = _completion(model=self.model, provider=self.provider, messages=messages)
            cur_message = res.choices[0].message
            self.total_cost += res._hidden_params.get("response_cost") or 0.0
            content = str(cur_message.content or "").strip()
            if verify(self.model, self.provider, content, messages):
                self.messages.append(cur_message.model_dump())
                return content
            attempts += 1
        assert cur_message is not None
        return str(cur_message.content or "").strip()


class ReflectionUserSimulationEnv(LLMUserSimulationEnv):
    def __init__(self, model: str, provider: Optional[str], max_attempts: int = 2) -> None:
        self.model = model
        self.provider = provider
        self.max_attempts = max_attempts
        self.total_cost = 0.0
        self.messages = []
        self.reset()

    def generate_next_message(self, messages: list[dict[str, Any]]) -> str:
        cur_messages = messages.copy()
        initial_response = super().generate_next_message(cur_messages)
        if verify(self.model, self.provider, initial_response, cur_messages):
            return initial_response
        attempts = 1
        while attempts < self.max_attempts:
            patched_response = reflect(self.model, self.provider, initial_response, cur_messages)
            if verify(self.model, self.provider, patched_response, cur_messages):
                return patched_response
            attempts += 1
        return initial_response


class UserStrategy(enum.Enum):
    HUMAN = "human"
    LLM = "llm"
    REACT = "react"
    VERIFY = "verify"
    REFLECTION = "reflection"


def get_user_strategy(name: Union[str, UserStrategy]) -> UserStrategy:
    if isinstance(name, UserStrategy):
        return name
    return UserStrategy(str(name).strip())


def _manual_structured_user_decision(prompt: str) -> StructuredUserDecision:
    print(prompt)
    raw_value = input("请输入结构化决策 JSON：\n")
    return parse_structured_user_decision(_extract_json_object_text(raw_value))


def generate_structured_user_decision(
    *,
    user_strategy: Union[str, UserStrategy],
    model: Optional[str],
    provider: Optional[str],
    prompt: str,
    context: AskUserDecisionContext | None = None,
    max_attempts: int = 2,
    instruction: str | None = None,
    transcript: str | None = None,
    trace_hook: StructuredDecisionTraceHook | None = None,
) -> StructuredUserDecision:
    strategy = get_user_strategy(user_strategy)
    if strategy == UserStrategy.HUMAN:
        return _manual_structured_user_decision(prompt)

    if context is not None and context.options and context.user_profile is not None:
        return generate_persona_user_decision(
            user_strategy=user_strategy,
            model=model,
            provider=provider,
            instruction=instruction or prompt,
            transcript=transcript or "",
            context=context,
            max_attempts=max_attempts,
            trace_hook=trace_hook,
        )

    if model is None:
        raise ValueError("AI 用户结构化决策需要配置 model")

    base_messages = [
        {
            "role": "system",
            "content": "你是 benchmark 中的 AI 模拟用户。你只能输出一个 JSON 对象，不要输出额外文字。",
        },
        {"role": "user", "content": prompt},
    ]
    messages = list(base_messages)
    last_error: Exception | None = None

    attempt_limit = max(1, int(max_attempts))
    for attempt_index in range(1, attempt_limit + 1):
        if trace_hook is not None:
            trace_hook(
                "用户模拟模型调用",
                {
                    "internal_attempt": attempt_index,
                    "attempt_limit": attempt_limit,
                    "strategy": strategy.value,
                    "model": model,
                    "provider": provider,
                },
            )
        res = _completion(
            model=model,
            provider=provider,
            messages=messages,
            extra_kwargs=_structured_decision_completion_kwargs(model=model, provider=provider),
        )
        raw_text = str(res.choices[0].message.content or "").strip()
        try:
            return parse_structured_user_decision(_extract_json_object_text(raw_text))
        except Exception as exc:
            last_error = exc
            if trace_hook is not None:
                trace_hook(
                    "用户模拟输出非法",
                    {
                        "internal_attempt": attempt_index,
                        "attempt_limit": attempt_limit,
                        "strategy": strategy.value,
                        "model": model,
                        "provider": provider,
                        "error": str(exc),
                        "raw_text": raw_text,
                    },
                )
            messages = [
                *base_messages,
                {"role": "assistant", "content": raw_text},
                {
                    "role": "user",
                    "content": "你上一条输出不是合法 JSON。请严格只输出一个 JSON 对象，并使用允许的 decision_kind。",
                },
            ]

    assert last_error is not None
    raise ValueError(f"structured user decision invalid: {last_error}") from last_error


def load_user(
    user_strategy: Union[str, UserStrategy],
    model: Optional[str] = DEFAULT_USER_MODEL,
    provider: Optional[str] = None,
) -> BaseUserSimulationEnv:
    strategy = get_user_strategy(user_strategy)
    if strategy == UserStrategy.HUMAN:
        return HumanUserSimulationEnv()
    if strategy == UserStrategy.LLM:
        if model is None:
            raise ValueError("LLM user strategy requires a model")
        return LLMUserSimulationEnv(model=model, provider=provider)
    if strategy == UserStrategy.REACT:
        if model is None:
            raise ValueError("React user strategy requires a model")
        return ReactUserSimulationEnv(model=model, provider=provider)
    if strategy == UserStrategy.VERIFY:
        if model is None:
            raise ValueError("Verify user strategy requires a model")
        return VerifyUserSimulationEnv(model=model, provider=provider)
    if strategy == UserStrategy.REFLECTION:
        if model is None:
            raise ValueError("Reflection user strategy requires a model")
        return ReflectionUserSimulationEnv(model=model, provider=provider)
    raise ValueError(f"Unknown user strategy {user_strategy}")
