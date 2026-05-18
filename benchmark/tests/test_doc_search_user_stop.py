from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.chat_export.render_first_attempt_review_html import build_html
from doc_search_bench.envs.doc_search.adapters import AdapterResult
from doc_search_bench.envs.doc_search.env import DocSearchBenchmarkEnv
from doc_search_bench.judges.trace import build_trace_analysis
from doc_search_bench.types import (
    RunConfig,
    TaskCase,
    UserProfile,
    build_case_run_result,
)
from doc_search_bench.user import (
    AskUserDecisionContext,
    AskUserOption,
    _CompatChoice,
    _CompatMessage,
    _CompatResponse,
    StructuredUserDecision,
    UserSimulationProviderError,
    build_persona_structured_decision_prompt,
    generate_persona_user_decision,
    parse_structured_user_decision,
)


def build_config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        split="dev",
        base_url="http://127.0.0.1:8000",
        app_token=None,
        timeout_ms=30_000,
        top_k=10,
        request_mode="doc_search",
        max_attempts_per_case=1,
        user_strategy="llm",
        user_model=None,
        user_provider=None,
        output_prefix=str(tmp_path / "out"),
    )


def build_task() -> TaskCase:
    return TaskCase(
        case_id="stop_case_001",
        split="dev",
        layer="atomic",
        suite_id="suite_demo",
        input_modality="text",
        question_text="老师，麻烦帮忙找下国六红岩杰狮H6 BCM的针脚定义图",
        question_images=[],
        vehicle_info=None,
        preprocess_strategy="none",
        benchmark_track="chat_completions",
        request_context={},
        accepted_titles=["红岩杰狮H6 BCM 针脚定义图"],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="",
        initial_user_message="麻烦帮忙找下国六红岩杰狮H6 BCM的针脚定义图。",
        user_profile=UserProfile(
            persona="normal",
            goal="找到国六红岩杰狮H6 BCM 的针脚定义图。",
            known_items=["国六", "红岩", "杰狮H6", "BCM", "针脚定义图"],
        ),
    )


def test_parse_structured_user_decision_requires_stop_reason_code():
    decision = parse_structured_user_decision(
        {
            "decision_kind": "stop",
            "stop_reason_code": "OPTION_SPACE_CONFLICT",
            "reason": "当前候选空间与红岩线索冲突",
            "evidence": {
                "supports": ["红岩", "BCM"],
                "conflicts": ["候选项全部是其他品牌"],
            },
        }
    )

    assert decision.decision_kind == "stop"
    assert decision.stop_reason_code == "OPTION_SPACE_CONFLICT"
    assert decision.evidence["supports"] == ["红岩", "BCM"]

    with pytest.raises(ValueError):
        parse_structured_user_decision({"decision_kind": "stop", "reason": "缺少 code"})


def test_build_persona_prompt_requires_self_check_for_generic_terms():
    prompt = build_persona_structured_decision_prompt(
        instruction="请严格按用户已知信息做选择",
        transcript="用户：这个电路图有没老师",
        context=AskUserDecisionContext(
            ask_user_question="请选择您需要的车辆品牌或型号",
            options=[
                AskUserOption(key="cummins", label="共轨原创"),
                AskUserOption(key="other", label="其他"),
            ],
            conversation_turn_count=1,
            scenario="normal",
            initial_user_message="这个电路图有没老师",
            user_profile=UserProfile(
                persona="normal",
                known_items=["康明斯系统", "ECU", "电路图"],
            ),
        ),
    )

    assert "允许做合理推断，但在输出前先自检" in prompt
    assert "能区分候选项的线索时，才允许选择具体项" in prompt
    assert "单独出现时不能直接支持某个具体品牌、型号、厂商或针数选项" in prompt


def test_build_persona_prompt_forbids_image_clues_for_image_parsing_required():
    prompt = build_persona_structured_decision_prompt(
        instruction="请严格按用户已知信息做选择",
        transcript="用户：查下这板子的资料",
        context=AskUserDecisionContext(
            ask_user_question="请选择品牌",
            options=[
                AskUserOption(key="vagon", label="华夏龙晖73针"),
                AskUserOption(key="other", label="其他"),
            ],
            conversation_turn_count=1,
            scenario="image_parsing_required",
            initial_user_message="查下这板子的资料",
            user_profile=UserProfile(
                persona="image_parsing_required",
                known_items=["板子资料"],
            ),
        ),
    )

    assert "用户视角不能从图片中得到任何新信息" in prompt


def test_build_persona_prompt_allows_explicit_image_clues_for_non_image_parsing_required():
    prompt = build_persona_structured_decision_prompt(
        instruction="请严格按用户已知信息做选择",
        transcript="用户：老师这个整车电路图资料有吗",
        context=AskUserDecisionContext(
            ask_user_question="请选择子系统",
            options=[
                AskUserOption(key="other", label="其他"),
                AskUserOption(key="doc", label="DOC"),
            ],
            conversation_turn_count=1,
            scenario="normal",
            initial_user_message="老师这个整车电路图资料有吗",
            user_profile=UserProfile(
                persona="normal",
                known_items=["整车电路图"],
            ),
        ),
    )

    assert "若当前场景不是 `image_parsing_required`" in prompt
    assert "图片中清晰可读且稳定的线索" in prompt


def test_generate_persona_user_decision_prefers_fallback_over_stop_when_option_space_is_sparse(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "doc_search_bench.user._completion",
        lambda **_: _CompatResponse(
            choices=[
                _CompatChoice(
                    message=_CompatMessage(
                        role="assistant",
                        content="""{
  "decision_kind": "choose_option",
  "selected_option_key": "other",
  "selected_option_label": "其他",
  "reason": "当前具体资料类型都不准确，其他更符合用户认知",
  "evidence": {
    "supports": ["博世878", "云内", "电脑板", "供电模块"],
    "conflicts": ["现有具体项都不足以准确表达当前诉求"]
  }
}""",
                    )
                )
            ]
        ),
    )
    decision = generate_persona_user_decision(
        user_strategy="llm",
        model="dummy-model",
        provider=None,
        instruction="请严格按用户已知信息做选择",
        transcript="用户：博世878云内的电脑板供电模块有吗",
        context=AskUserDecisionContext(
            ask_user_question="请选择资料类型",
            options=[
                AskUserOption(key="whole_vehicle", label="整车电路图"),
                AskUserOption(key="wire_harness", label="线束图"),
                AskUserOption(key="fuse_box", label="保险盒定义"),
                AskUserOption(key="ecu_schema", label="ECU原理图"),
                AskUserOption(key="other", label="其他"),
            ],
            conversation_turn_count=1,
            scenario="normal",
            user_profile=UserProfile(
                persona="normal",
                known_items=["云内", "博世878", "电脑板", "供电模块"],
            ),
        ),
        trace_hook=None,
    )

    assert decision.decision_kind == "choose_option"
    assert decision.selected_option_key == "other"
    assert decision.selected_option_label == "其他"
    assert decision.stop_reason_code is None


def test_generate_persona_user_decision_stops_when_no_fallback_and_user_cannot_answer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "doc_search_bench.user._completion",
        lambda **_: _CompatResponse(
            choices=[
                _CompatChoice(
                    message=_CompatMessage(
                        role="assistant",
                        content="""{
  "decision_kind": "stop",
  "stop_reason_code": "INSUFFICIENT_INFORMATION",
  "reason": "用户不知道当前需要确认的资料类型，且没有兜底项",
  "evidence": {
    "supports": ["博世878", "云内", "电脑板", "供电模块"],
    "conflicts": ["当前问题要求确认资料类型，但用户并不掌握", "当前没有其他/不确定类兜底项"]
  }
}""",
                    )
                )
            ]
        ),
    )
    decision = generate_persona_user_decision(
        user_strategy="llm",
        model="dummy-model",
        provider=None,
        instruction="请严格按用户已知信息做选择",
        transcript="用户：博世878云内的电脑板供电模块有吗",
        context=AskUserDecisionContext(
            ask_user_question="请选择资料类型",
            options=[
                AskUserOption(key="whole_vehicle", label="整车电路图"),
                AskUserOption(key="wire_harness", label="线束图"),
                AskUserOption(key="fuse_box", label="保险盒定义"),
                AskUserOption(key="ecu_schema", label="ECU原理图"),
            ],
            conversation_turn_count=1,
            scenario="normal",
            user_profile=UserProfile(
                persona="normal",
                known_items=["云内", "博世878", "电脑板", "供电模块"],
            ),
        ),
        trace_hook=None,
    )

    assert decision.decision_kind == "stop"
    assert decision.stop_reason_code == "INSUFFICIENT_INFORMATION"


def test_generate_persona_user_decision_uses_json_mode_for_openrouter(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _CompatResponse(
            choices=[
                _CompatChoice(
                    message=_CompatMessage(
                        role="assistant",
                        content="""{
  "decision_kind": "choose_option",
  "selected_option_key": "other",
  "selected_option_label": "其他",
  "reason": "兜底项",
  "evidence": {}
}""",
                    )
                )
            ]
        )

    monkeypatch.setattr("doc_search_bench.user._completion", fake_completion)
    generate_persona_user_decision(
        user_strategy="llm",
        model="openrouter:deepseek/deepseek-chat-v3-0324",
        provider=None,
        instruction="请严格按用户已知信息做选择",
        transcript="用户：博世878云内的电脑板供电模块有吗",
        context=AskUserDecisionContext(
            ask_user_question="请选择资料类型",
            options=[
                AskUserOption(key="whole_vehicle", label="整车电路图"),
                AskUserOption(key="wire_harness", label="线束图"),
                AskUserOption(key="fuse_box", label="保险盒定义"),
                AskUserOption(key="other", label="其他"),
            ],
            conversation_turn_count=1,
            scenario="normal",
            user_profile=UserProfile(
                persona="normal",
                known_items=["云内", "博世878", "电脑板", "供电模块"],
            ),
        ),
        trace_hook=None,
    )

    assert captured["extra_kwargs"]["response_format"] == {"type": "json_object"}
    assert captured["extra_kwargs"]["temperature"] == 0


def test_run_chat_case_supports_user_simulation_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env = DocSearchBenchmarkEnv(config=build_config(tmp_path), benchmark_root=tmp_path, run_id="run-stop")
    task = build_task()
    result = build_case_run_result(task, "run-stop")
    adapter_calls: list[str] = []

    def fake_execute(call):
        adapter_calls.append(call.endpoint)
        return AdapterResult(
            endpoint=call.endpoint,
            request_payload=call.payload,
            http_status=200,
            raw_body={
                "type": "ask_user",
                "session_id": "session-1",
                "business": "DOC_SEARCH",
                "content": {
                    "question": "请选择品牌",
                    "tool_call_id": "tool-1",
                    "options": [
                        {"key": "dongfeng", "label": "东风", "selection_payload": {"id": 1}},
                        {"key": "qingdao", "label": "青岛解放", "selection_payload": {"id": 2}},
                        {"key": "other", "label": "其他品牌", "selection_payload": {"id": 3}},
                    ],
                },
            },
        )

    monkeypatch.setattr(env.adapter, "execute", fake_execute)
    monkeypatch.setattr(
        env,
        "request_structured_decision",
        lambda **_: StructuredUserDecision(
            decision_kind="stop",
            stop_reason_code="OPTION_SPACE_CONFLICT",
            reason="当前候选空间与红岩杰狮H6线索冲突",
            evidence={
                "supports": ["红岩", "杰狮H6", "BCM"],
                "conflicts": ["当前只提供了其他品牌候选"],
            },
        ),
    )

    env.run_chat_case(task, result)

    assert adapter_calls == ["http://127.0.0.1:8000/chat/completions"]
    assert result.response.final_status == "stopped_by_user_simulation"
    assert result.workflow.stop_reason == "user_simulation_stop"
    assert result.workflow.stopped_by_user_simulation is True
    assert result.workflow.simulation_stop_count == 1
    assert result.workflow.turns[0].user_decision_kind == "stop"
    assert result.workflow.turns[0].user_stop_reason_code == "OPTION_SPACE_CONFLICT"
    assert result.workflow.turns[0].user_decision_evidence["supports"] == ["红岩", "杰狮H6", "BCM"]


def test_trace_and_review_render_stop_details(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env = DocSearchBenchmarkEnv(config=build_config(tmp_path), benchmark_root=tmp_path, run_id="run-review")
    task = build_task()
    result = build_case_run_result(task, "run-review")

    monkeypatch.setattr(
        env.adapter,
        "execute",
        lambda call: AdapterResult(
            endpoint=call.endpoint,
            request_payload=call.payload,
            http_status=200,
            raw_body={
                "type": "ask_user",
                "session_id": "session-2",
                "business": "DOC_SEARCH",
                "content": {
                    "question": "请选择品牌",
                    "tool_call_id": "tool-2",
                    "options": [
                        {"key": "dongfeng", "label": "东风", "selection_payload": {"id": 1}},
                        {"key": "other", "label": "其他品牌", "selection_payload": {"id": 2}},
                    ],
                },
            },
        ),
    )
    monkeypatch.setattr(
        env,
        "request_structured_decision",
        lambda **_: StructuredUserDecision(
            decision_kind="stop",
            stop_reason_code="OPTION_SPACE_CONFLICT",
            reason="当前品牌选项与红岩杰狮H6线索明显冲突",
            evidence={
                "supports": ["红岩", "杰狮H6"],
                "conflicts": ["当前品牌选项与用户已知线索不相容"],
            },
        ),
    )

    env.run_chat_case(task, result)
    trace = build_trace_analysis(task, result)

    assert trace["stopped_by_user_simulation"] is True
    assert trace["simulation_stop_count"] == 1
    assert trace["simulation_valid_stop"] is True
    assert trace["user_stop_reason_code"] == "OPTION_SPACE_CONFLICT"
    assert trace["failure_reason"] == "system_clarification_failure"

    html_output = build_html(
        [result.to_dict()],
        {
            task.case_id: {
                "question_images": [],
                "user_profile": {
                    "goal": task.user_profile.goal,
                    "known_items": task.user_profile.known_items,
                },
            }
        },
        tmp_path / "first_attempt_review.html",
    )

    assert "OPTION_SPACE_CONFLICT" in html_output
    assert "红岩" in html_output
    assert "known_items" in html_output


def test_run_chat_case_treats_user_sim_provider_failure_as_error_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    env = DocSearchBenchmarkEnv(config=build_config(tmp_path), benchmark_root=tmp_path, run_id="run-user-error")
    task = build_task()
    result = build_case_run_result(task, "run-user-error")

    monkeypatch.setattr(
        env.adapter,
        "execute",
        lambda call: AdapterResult(
            endpoint=call.endpoint,
            request_payload=call.payload,
            http_status=200,
            raw_body={
                "type": "ask_user",
                "session_id": "session-3",
                "business": "DOC_SEARCH",
                "content": {
                    "question": "请选择品牌",
                    "tool_call_id": "tool-3",
                    "options": [
                        {"key": "dongfeng", "label": "东风", "selection_payload": {"id": 1}},
                        {"key": "other", "label": "其他品牌", "selection_payload": {"id": 2}},
                    ],
                },
            },
        ),
    )
    monkeypatch.setattr(
        env,
        "request_structured_decision",
        lambda **_: (_ for _ in ()).throw(
            UserSimulationProviderError("litellm.APIError: OpenrouterException - SSL EOF")
        ),
    )

    env.run_chat_case(task, result)

    assert result.response.response_type == "error"
    assert result.response.final_status == "error_http"
    assert result.workflow.stop_reason == "error"
    assert result.workflow.turns[0].user_decision_kind == "error"
    assert "SSL EOF" in str(result.response.raw_summary)
