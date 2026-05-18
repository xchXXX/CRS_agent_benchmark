from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_DIRECT_PROVIDER_HINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "google": ("google-gla", ("GEMINI_API_KEY",)),
    "openai": ("openai", ("OPENAI_API_KEY",)),
    "anthropic": ("anthropic", ("ANTHROPIC_API_KEY",)),
    "deepseek": ("deepseek", ("DEEPSEEK_API_KEY",)),
    "groq": ("groq", ("GROQ_API_KEY",)),
    "xai": ("xai", ("XAI_API_KEY",)),
}
_KNOWN_PROVIDER_PREFIXES = (
    "openrouter:",
    "google-gla:",
    "openai:",
    "anthropic:",
    "deepseek:",
    "groq:",
    "xai:",
    "ollama:",
)


@dataclass(frozen=True)
class ResolvedUserModelDefaults:
    model: str
    provider: str | None
    source: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def backend_env_files() -> tuple[Path, Path]:
    backend_dir = repo_root() / "modules" / "crs-agent-upstream" / "backend"
    return backend_dir / ".env", backend_dir / ".env.runtime"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values.setdefault(key, value)
    return values


def load_backend_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in backend_env_files():
        for key, value in _parse_env_file(path).items():
            merged.setdefault(key, value)
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    return merged


def _first_non_empty(env: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = env.get(key)
        if value and value.strip():
            return value.strip()
    return ""


def _has_any_env(env: dict[str, str], keys: tuple[str, ...]) -> bool:
    return any(bool(_first_non_empty(env, key)) for key in keys)


def _has_openrouter_credentials(env: dict[str, str]) -> bool:
    return bool(_first_non_empty(env, "OPENROUTER_API_KEY", "CRS_OPENROUTER_API_KEY"))


def _normalize_backend_model(raw_model: str, env: dict[str, str]) -> tuple[str, str | None]:
    model = raw_model.strip()
    if not model:
        return "gpt-4o", None

    for prefix in _KNOWN_PROVIDER_PREFIXES:
        if model.startswith(prefix):
            if prefix == "ollama:":
                _, _, stripped_model = model.partition(":")
                return stripped_model.strip() or "gpt-4o", "ollama"
            return model, None

    if "/" in model:
        vendor, _, raw_model_name = model.partition("/")
        vendor = vendor.strip().lower()
        model_name = raw_model_name.strip()
        if vendor and model_name:
            provider_info = _DIRECT_PROVIDER_HINTS.get(vendor)
            if provider_info is not None:
                provider_name, env_keys = provider_info
                if _has_any_env(env, env_keys):
                    return f"{provider_name}:{model_name}", None
                if _has_openrouter_credentials(env):
                    return f"openrouter:{vendor}/{model_name}", None
                return f"{provider_name}:{model_name}", None
        return f"openrouter:{model}", None

    return model, None


def resolve_user_model_defaults() -> ResolvedUserModelDefaults:
    env = load_backend_env()
    configured_model = _first_non_empty(
        env,
        "CRS_OPENROUTER_CLARIFY_MODEL",
        "OPENROUTER_CLARIFY_MODEL",
        "CRS_AGENT_MODEL",
        "AGENT_MODEL",
    )
    if configured_model:
        model, provider = _normalize_backend_model(configured_model, env)
        return ResolvedUserModelDefaults(
            model=model,
            provider=provider,
            source="backend_clarify_or_agent_model",
        )

    fallback_model = _first_non_empty(env, "BENCHMARK_USER_MODEL") or "gpt-4o"
    fallback_provider = _first_non_empty(env, "BENCHMARK_USER_PROVIDER") or None
    return ResolvedUserModelDefaults(
        model=fallback_model,
        provider=fallback_provider,
        source="benchmark_or_builtin_fallback",
    )


def apply_backend_llm_env_defaults() -> dict[str, str]:
    env = load_backend_env()
    applied: dict[str, str] = {}
    for target_key, source_keys in (
        ("OPENROUTER_API_KEY", ("OPENROUTER_API_KEY", "CRS_OPENROUTER_API_KEY")),
        ("OPENROUTER_BASE_URL", ("OPENROUTER_BASE_URL", "CRS_OPENROUTER_BASE_URL")),
    ):
        if os.environ.get(target_key):
            continue
        value = _first_non_empty(env, *source_keys)
        if not value:
            continue
        os.environ[target_key] = value
        applied[target_key] = value
    return applied
