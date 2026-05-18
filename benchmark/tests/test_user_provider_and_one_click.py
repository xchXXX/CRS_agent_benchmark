from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run as benchmark_run
from doc_search_bench import user as user_module
from doc_search_bench.user import (
    UserSimulationProviderError,
    _CompletionClientBundle,
    _completion,
    _resolve_completion_target,
)


def test_resolve_completion_target_infers_openrouter_provider():
    model, provider = _resolve_completion_target(
        "openrouter:deepseek/deepseek-chat-v3-0324",
        None,
    )

    assert model == "openrouter/deepseek/deepseek-chat-v3-0324"
    assert provider is None


def test_resolve_completion_target_infers_direct_provider_prefix():
    model, provider = _resolve_completion_target(
        "google-gla:gemini-3.1-flash-lite-preview",
        None,
    )

    assert model == "gemini-3.1-flash-lite-preview"
    assert provider == "google-gla"


def test_resolve_completion_target_normalizes_explicit_openrouter_provider():
    model, provider = _resolve_completion_target(
        "deepseek/deepseek-chat-v3-0324",
        "openrouter",
    )

    assert model == "openrouter/deepseek/deepseek-chat-v3-0324"
    assert provider is None


def test_completion_passes_openrouter_model_without_custom_provider(monkeypatch):
    captured: dict[str, object] = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            _hidden_params={},
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=fake_completion))
    monkeypatch.setattr(user_module, "_build_completion_client", lambda **_: None)

    _completion(
        model="openrouter:deepseek/deepseek-chat-v3-0324",
        provider=None,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert captured["model"] == "openrouter/deepseek/deepseek-chat-v3-0324"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["custom_llm_provider"] is None


def test_completion_uses_fresh_openrouter_client_and_closes(monkeypatch):
    captured: dict[str, object] = {}
    bundle_state = {"closed": False}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            _hidden_params={},
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=fake_completion))
    monkeypatch.setattr(
        user_module,
        "_build_completion_client",
        lambda **_: _CompletionClientBundle(
            client="fresh-client",
            close=lambda: bundle_state.__setitem__("closed", True),
        ),
    )

    _completion(
        model="openrouter:deepseek/deepseek-chat-v3-0324",
        provider=None,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert captured["client"] == "fresh-client"
    assert bundle_state["closed"] is True


def test_completion_wraps_provider_exception(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(completion=lambda **_: (_ for _ in ()).throw(RuntimeError("ssl eof"))),
    )
    monkeypatch.setattr(user_module, "_build_completion_client", lambda **_: None)

    with pytest.raises(UserSimulationProviderError, match="ssl eof"):
        _completion(
            model="openrouter:deepseek/deepseek-chat-v3-0324",
            provider=None,
            messages=[{"role": "user", "content": "hi"}],
        )


def test_extract_incomplete_execution_count_counts_invalid_user_decision(tmp_path: Path):
    report_path = tmp_path / "report.score.json"
    report_path.write_text(
        """{
  "summary": {
    "attempt_level": {
      "failures": {
        "final_status_counts": {
          "stopped_invalid_user_decision": 2,
          "success_documents": 3
        }
      }
    }
  }
}""",
        encoding="utf-8",
    )

    assert benchmark_run._extract_incomplete_execution_count(report_path) == 2


def test_build_benchmark_command_omits_empty_user_provider_and_keeps_filters():
    args = argparse.Namespace(
        split="train",
        timeout_ms=240000,
        max_attempts_per_case=1,
        user_model="openrouter:deepseek/deepseek-chat-v3-0324",
        user_provider=None,
        suite=["real_world_wecom_train"],
        case_id=["real_train_0003"],
    )

    cmd = benchmark_run._build_benchmark_command(
        base_url="http://127.0.0.1:8006",
        token="demo-token",
        args=args,
    )

    assert "--user-provider" not in cmd
    assert cmd.count("--suite") == 1
    assert cmd.count("--case-id") == 1
    assert "real_world_wecom_train" in cmd
    assert "real_train_0003" in cmd


def test_extract_json_object_ignores_leading_litellm_noise():
    raw_stdout = """
Give Feedback / Get Help: https://github.com/BerriAI/litellm/issues/new
LiteLLM.Info: debug hint

{
  "benchmark_slug": "doc-search-benchmark",
  "score_report": "C:\\\\demo\\\\report.score.json"
}
"""

    parsed = benchmark_run._extract_json_object(raw_stdout)

    assert parsed == {
        "benchmark_slug": "doc-search-benchmark",
        "score_report": "C:\\demo\\report.score.json",
    }


def test_resolve_probe_target_uses_selected_image_case():
    target = benchmark_run._resolve_probe_target(
        repo_root=Path(__file__).resolve().parents[2],
        split="train",
        suite_filters=["real_world_wecom_train"],
        case_filters=["real_train_0003"],
    )

    assert target is not None
    image_path, probe_question, case_id = target
    assert case_id == "real_train_0003"
    assert image_path.exists()
    assert "TH7" in probe_question


def test_resolve_backend_model_defaults_falls_back_to_agent_model(monkeypatch):
    monkeypatch.setattr(
        benchmark_run,
        "load_backend_env",
        lambda: {
            "CRS_AGENT_MODEL": "openrouter:deepseek/deepseek-chat-v3-0324",
        },
    )

    resolved = benchmark_run._resolve_backend_model_defaults()

    assert resolved == {
        "agent_model": "openrouter:deepseek/deepseek-chat-v3-0324",
        "openrouter_clarify_model": "openrouter:deepseek/deepseek-chat-v3-0324",
        "intent_router_model": "openrouter:deepseek/deepseek-chat-v3-0324",
        "coding_engine_model": "openrouter:deepseek/deepseek-chat-v3-0324",
    }


def test_proxy_env_overrides_cover_upper_and_lowercase():
    overrides = benchmark_run._proxy_env_overrides("http://127.0.0.1:7897")

    assert overrides["http_proxy"] == "http://127.0.0.1:7897"
    assert overrides["https_proxy"] == "http://127.0.0.1:7897"
    assert overrides["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert overrides["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert overrides["ALL_PROXY"] == "http://127.0.0.1:7897"
    assert overrides["all_proxy"] == "http://127.0.0.1:7897"
    assert overrides["NO_PROXY"] == "wx.51gonggui.com,127.0.0.1,localhost"
    assert overrides["no_proxy"] == "wx.51gonggui.com,127.0.0.1,localhost"


def test_build_child_env_inherits_proxy_and_model_overrides(monkeypatch):
    monkeypatch.setenv("EXISTING_ENV_FOR_TEST", "kept")

    env = benchmark_run._build_child_env(
        proxy_url="http://127.0.0.1:7897",
        model_defaults={
            "agent_model": "openrouter:deepseek/deepseek-chat-v3-0324",
            "openrouter_clarify_model": "openrouter:deepseek/deepseek-chat-v3-0324",
            "intent_router_model": "openrouter:deepseek/deepseek-chat-v3-0324",
            "coding_engine_model": "openrouter:deepseek/deepseek-chat-v3-0324",
        },
    )

    assert env["EXISTING_ENV_FOR_TEST"] == "kept"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert env["ALL_PROXY"] == "http://127.0.0.1:7897"
    assert env["CRS_AGENT_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_OPENROUTER_CLARIFY_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_INTENT_ROUTER_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_CODING_ENGINE_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["BENCHMARK_USER_OPENAI_COMPAT_FRESH_CLIENT"] == "1"


def test_start_backend_injects_runtime_model_env(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class DummyProc:
        pass

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["env"] = dict(kwargs["env"])
        return DummyProc()

    monkeypatch.setattr(benchmark_run.subprocess, "Popen", fake_popen)

    proc = benchmark_run._start_backend(
        backend_dir=tmp_path,
        backend_port=8006,
        image_model="qwen/qwen3-vl-32b-instruct",
        image_max_images=8,
        proxy_url="http://127.0.0.1:7897",
        model_defaults={
            "agent_model": "openrouter:deepseek/deepseek-chat-v3-0324",
            "openrouter_clarify_model": "openrouter:deepseek/deepseek-chat-v3-0324",
            "intent_router_model": "openrouter:deepseek/deepseek-chat-v3-0324",
            "coding_engine_model": "openrouter:deepseek/deepseek-chat-v3-0324",
        },
        stdout_log_path=tmp_path / "backend.stdout.log",
        stderr_log_path=tmp_path / "backend.stderr.log",
    )

    assert isinstance(proc, DummyProc)
    assert captured["cwd"] == str(tmp_path)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CRS_AGENT_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_OPENROUTER_CLARIFY_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_INTENT_ROUTER_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_CODING_ENGINE_MODEL"] == "openrouter:deepseek/deepseek-chat-v3-0324"
    assert env["CRS_IMAGE_EVIDENCE_MODEL"] == "qwen/qwen3-vl-32b-instruct"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert env["ALL_PROXY"] == "http://127.0.0.1:7897"


def test_make_one_click_run_id_has_expected_prefix():
    run_id = benchmark_run._make_one_click_run_id()

    assert run_id.startswith("one-click-")
    assert len(run_id) == len("one-click-20260518T020939Z")
