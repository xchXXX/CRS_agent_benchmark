"""Helpers for normalizing configured model identifiers."""

from __future__ import annotations

import os
from typing import Any

try:
    from pydantic_ai.models import parse_model_id
except Exception:  # pragma: no cover - keeps config bootstrap usable without pydantic_ai
    parse_model_id = None  # type: ignore[assignment]


_DIRECT_PROVIDER_HINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "google": ("google-gla", ("GEMINI_API_KEY",)),
    "openai": ("openai", ("OPENAI_API_KEY",)),
    "anthropic": ("anthropic", ("ANTHROPIC_API_KEY",)),
    "deepseek": ("deepseek", ("DEEPSEEK_API_KEY",)),
    "groq": ("groq", ("GROQ_API_KEY",)),
    "xai": ("xai", ("XAI_API_KEY",)),
}


def normalize_configured_model(model: Any) -> Any:
    """Normalize shorthand model ids to provider-qualified ids when possible."""

    if not isinstance(model, str):
        return model

    normalized = model.strip()
    if not normalized:
        return normalized

    if parse_model_id is not None:
        provider, _ = parse_model_id(normalized)
        if provider == "openrouter":
            redirected = _maybe_redirect_openrouter_model(normalized)
            return redirected or normalized
        openrouter_redirected = _maybe_promote_direct_provider_model_to_openrouter(normalized, provider)
        if openrouter_redirected is not None:
            return openrouter_redirected
        if provider is not None:
            return normalized

    direct_prefixed = _prefix_vendor_model_shorthand(normalized)
    if direct_prefixed:
        return direct_prefixed

    if "/" in normalized:
        return f"openrouter:{normalized}"

    return normalized


def _prefix_vendor_model_shorthand(model: str) -> str | None:
    if "/" not in model:
        return None

    vendor, _, raw_model_name = model.partition("/")
    vendor = vendor.strip().lower()
    model_name = raw_model_name.strip()
    if not vendor or not model_name:
        return None

    provider_info = _DIRECT_PROVIDER_HINTS.get(vendor)
    if provider_info is None:
        return None

    provider, env_keys = provider_info
    if _has_any_env(env_keys):
        return f"{provider}:{model_name}"

    if os.getenv("OPENROUTER_API_KEY"):
        return f"openrouter:{vendor}/{model_name}"

    return f"{provider}:{model_name}"


def _maybe_redirect_openrouter_model(model: str) -> str | None:
    if not model.startswith("openrouter:"):
        return None
    if _has_openrouter_credentials():
        return None

    _, _, raw_model_name = model.partition(":")
    redirected = _prefix_vendor_model_shorthand(raw_model_name)
    if redirected and not redirected.startswith("openrouter:"):
        return redirected
    return None


def _maybe_promote_direct_provider_model_to_openrouter(model: str, provider: str | None) -> str | None:
    vendor = _provider_to_vendor(provider)
    if vendor is None or not _has_openrouter_credentials():
        return None

    _, _, raw_model_name = model.partition(":")
    raw_model_name = raw_model_name.strip()
    if not raw_model_name:
        return None
    return f"openrouter:{vendor}/{raw_model_name}"


def _provider_to_vendor(provider: str | None) -> str | None:
    if not provider:
        return None

    for vendor, (candidate_provider, _) in _DIRECT_PROVIDER_HINTS.items():
        if candidate_provider == provider:
            return vendor
    return None


def _has_openrouter_credentials() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def _has_any_env(keys: tuple[str, ...]) -> bool:
    return any(bool(os.getenv(key)) for key in keys)
