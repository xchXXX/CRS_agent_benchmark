from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.user_model_defaults import (
    DEFAULT_BENCHMARK_USER_MODEL,
    apply_backend_llm_env_defaults,
    _normalize_backend_model,
    resolve_user_model_defaults,
)


def test_resolve_user_model_defaults_prefers_backend_clarify_model(monkeypatch):
    monkeypatch.setattr(
        "doc_search_bench.user_model_defaults.load_backend_env",
        lambda: {
            "CRS_OPENROUTER_CLARIFY_MODEL": "openrouter:deepseek/deepseek-chat-v3-0324",
            "CRS_AGENT_MODEL": "openrouter:google/gemini-3.1-flash-lite-preview",
        },
    )

    resolved = resolve_user_model_defaults()

    assert resolved.model == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert resolved.provider is None
    assert resolved.source == "backend_clarify_or_agent_model"


def test_resolve_user_model_defaults_falls_back_to_backend_agent_model(monkeypatch):
    monkeypatch.setattr(
        "doc_search_bench.user_model_defaults.load_backend_env",
        lambda: {
            "CRS_AGENT_MODEL": "openrouter:google/gemini-3.1-flash-lite-preview",
        },
    )

    resolved = resolve_user_model_defaults()

    assert resolved.model == "openrouter:google/gemini-3.1-flash-lite-preview"
    assert resolved.provider is None


def test_resolve_user_model_defaults_falls_back_to_builtin_openrouter_model(monkeypatch):
    monkeypatch.setattr(
        "doc_search_bench.user_model_defaults.load_backend_env",
        lambda: {},
    )

    resolved = resolve_user_model_defaults()

    assert resolved.model == DEFAULT_BENCHMARK_USER_MODEL
    assert resolved.provider is None
    assert resolved.source == "benchmark_or_builtin_fallback"


def test_normalize_backend_model_promotes_vendor_shorthand_to_openrouter_when_key_exists():
    model, provider = _normalize_backend_model(
        "google/gemini-3.1-flash-lite-preview",
        {"OPENROUTER_API_KEY": "masked"},
    )

    assert model == "openrouter:google/gemini-3.1-flash-lite-preview"
    assert provider is None


def test_normalize_backend_model_maps_ollama_prefix_to_provider():
    model, provider = _normalize_backend_model(
        "ollama:qwen2.5:7b",
        {},
    )

    assert model == "qwen2.5:7b"
    assert provider == "ollama"


def test_apply_backend_llm_env_defaults_promotes_openrouter_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "doc_search_bench.user_model_defaults.load_backend_env",
        lambda: {
            "OPENROUTER_API_KEY": "masked-openrouter-key",
        },
    )

    applied = apply_backend_llm_env_defaults()

    assert applied == {"OPENROUTER_API_KEY": "masked-openrouter-key"}
