import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.runtime.factory import AgentFactoryStatus
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.models.events import AgentEventType
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.intent_router import RequestIntentRouter
from app.agent.runtime.intent_router import IntentDecision, RoutedIntent
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings, settings
from app.legacy.services.diagnosis import get_fault_code_parser
from app.main import create_app
from app.schemas.chat import AskUserAnswer, ChatRequest


def build_test_deps(tmp_path) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
    )


class FakeParameterQueryService:
    def query(self, query: str, selection_payload=None, raw_query=None):
        del raw_query
        del selection_payload
        return {
            "status": "ok",
            "data": {
                "matched": True,
                "query": query,
                "summary": "K46 的针脚定义为 信号。",
                "requested_field": "pin_definition",
                "requested_field_label": "针脚定义",
                "selected_source": {
                    "id": "159",
                    "title": "EDC17C53针脚电压(12V系统)",
                    "ecu_name": "EDC17C53",
                    "system_voltage": 12,
                    "pin_doc_kind": "pin_voltage",
                },
                "rows": [
                    {
                        "id": "1",
                        "row_no": 1,
                        "component_name": "点火开关T15",
                        "ecu_pin_no": "K46",
                        "pin_definition": "信号",
                        "open_voltage_text": "12V",
                        "static_voltage_text": "12V",
                        "idle_voltage_text": "12V",
                        "requested_value": "信号",
                    }
                ],
                "source_refs": [
                    {
                        "id": "159",
                        "title": "EDC17C53针脚电压(12V系统)",
                        "relation": "primary",
                        "match_score": 1.0,
                    }
                ],
            },
        }

    async def query_async(self, query: str, selection_payload=None, raw_query=None):
        return self.query(query, selection_payload=selection_payload, raw_query=raw_query)


class FakeParameterQueryClarifyService:
    async def query_async(self, query: str, selection_payload=None, raw_query=None):
        del raw_query
        filters = dict((selection_payload or {}).get("filters") or {})
        if filters.get("param_source_id") == "159":
            return {
                "status": "ok",
                "data": {
                    "matched": True,
                    "query": query,
                    "summary": "油门踏板1 开路电压 0V。",
                    "requested_field": "open_voltage",
                    "requested_field_label": "开路电压",
                    "selected_source": {
                        "id": "159",
                        "title": "易控F17针脚电压",
                        "ecu_name": "易控F17",
                        "system_voltage": 5,
                        "pin_doc_kind": "pin_voltage",
                    },
                    "rows": [
                        {
                            "id": "1",
                            "row_no": 1,
                            "component_name": "油门踏板1",
                            "ecu_pin_no": "APP1",
                            "pin_definition": "油门踏板1信号",
                            "open_voltage_text": "0V",
                            "requested_value": "0V",
                        }
                    ],
                    "source_refs": [
                        {
                            "id": "159",
                            "title": "易控F17针脚电压",
                            "relation": "primary",
                            "match_score": 1.0,
                        }
                    ],
                },
            }

        return {
            "status": "need_clarify",
            "data": {"matched": False, "query": query, "clarify_type": "source"},
            "clarify": {
                "source": "parameter_query",
                "question": "请先确认 ECU 型号",
                "results_count": 1,
                "context": {
                    "scene": "parameter_query",
                    "clarify_type": "source",
                    "query": query,
                    "input_hint": "也可以直接输入 ECU 型号",
                },
                "options": [
                    {
                        "key": "159",
                        "label": "易控F17针脚电压",
                        "description": "5V · APP1",
                        "selection_payload": {
                            "filters": {"param_source_id": "159", "param_field": "open_voltage"},
                            "file_ids": [],
                        },
                    }
                ],
            },
        }


class FakeConfigService:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


def extract_request_text(messages: list[ModelMessage]) -> str:
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    return "\n".join(
        part.content
        for part in request.parts
        if isinstance(getattr(part, "content", None), str)
    )


async def collect_stream_events(service: AgentLoopService, request: ChatRequest):
    return [event async for event in service.stream(request)]


def test_agent_loop_service_returns_text_response(tmp_path):
    deps = build_test_deps(tmp_path)
    factory = AgentFactory(
        settings=Settings(agent_model="test", agent_test_output_text="runtime-ok"),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="hello agent")))

    assert response.type == "message"
    assert response.content == "runtime-ok"
    assert response.business == "GENERAL_CHAT"
    assert response.session_id
    assert deps.message_history_store.load_serialized_history(response.session_id) is not None
    assert response.metadata["llm"]["model_name"] == "crs-test"
    assert response.metadata["llm"]["call_count"] == 1
    assert response.metadata["llm"]["aggregate_usage"]["output_tokens"] >= 0
    assert response.metadata["llm"]["calls"][0]["phase"] == "agent_loop"


def test_agent_loop_service_aggregates_intent_router_and_agent_llm_metadata(tmp_path, monkeypatch):
    deps = build_test_deps(tmp_path)

    async def fake_route_async(self, message: str, mode: str | None = None):
        observer = getattr(self, "_llm_observer", None)
        if observer is not None:
            observer(
                SimpleNamespace(
                    response=SimpleNamespace(
                        model_name="router-model",
                        provider_name="openai",
                        provider_url=None,
                        provider_response_id="resp-router",
                        finish_reason="stop",
                        timestamp=None,
                        run_id="router-run",
                    ),
                    usage=lambda: SimpleNamespace(
                        input_tokens=12,
                        output_tokens=3,
                        total_tokens=15,
                        requests=1,
                        tool_calls=0,
                        details={},
                    ),
                ),
                0.0,
                "intent_router",
            )
        return IntentDecision(
            intent=RoutedIntent.GENERAL_CHAT,
            reason="router_llm",
            source="llm",
            confidence=0.91,
        )

    monkeypatch.setattr(RequestIntentRouter, "route_async", fake_route_async)
    factory = AgentFactory(
        settings=Settings(agent_model="test", agent_test_output_text="runtime-ok"),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="hello agent")))

    llm = response.metadata["llm"]
    assert llm["call_count"] == 2
    assert llm["model_name"] == "crs-test"
    assert llm["provider_name"] == "openai"
    assert llm["aggregate_usage"]["request_count"] == 2
    assert llm["aggregate_usage"]["input_tokens"] >= 12
    assert [call["phase"] for call in llm["calls"]] == ["intent_router", "agent_loop"]


def test_agent_loop_service_handles_ask_user_resume_flow(tmp_path):
    deps = build_test_deps(tmp_path)

    def ask_user_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请选择车型",
                            "input_type": "single_select",
                            "options": [{"key": "j6", "label": "解放 J6", "description": "重卡平台"}],
                            "allow_free_input": True,
                            "input_hint": "也可以直接输入车型",
                        },
                        tool_call_id="ask_user_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        return ModelResponse(parts=[TextPart(content=f"收到用户补充: {tool_return.content['answer']}")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(ask_user_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="这个问题需要先澄清车型", mode="general_chat")))

    assert first.type == "ask_user"
    assert first.need_clarify is True
    assert first.ask_user is not None
    assert first.ask_user.tool_call_id == "ask_user_1"
    assert first.ask_user.question == "请选择车型"
    assert first.clarify_options[0].key == "j6"
    assert deps.deferred_state_store.load(first.session_id, "ask_user_1") is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                message="",
                ask_user_answer=AskUserAnswer(tool_call_id="ask_user_1", answer="解放 J6"),
            )
        )
    )

    assert second.type == "message"
    assert second.content == "收到用户补充: 解放 J6"
    assert second.session_id == first.session_id


def test_agent_loop_service_rebuilds_agent_per_request_for_hot_config(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"agent_model": "model-v1"})

    class CountingFactory:
        def __init__(self):
            self.create_calls = 0

        @staticmethod
        def get_status():
            return AgentFactoryStatus(available=True, reason="ok", version="test")

        def create_agent(self, deps):
            self.create_calls += 1

            class DummyRunResult:
                output = str(deps.config_service.get("agent_model"))

                @staticmethod
                def all_messages_json():
                    return b"[]"

            class DummyAgent:
                async def run(self, **kwargs):
                    return DummyRunResult()

            return DummyAgent()

    factory = CountingFactory()
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="hello")))
    deps.config_service._values["agent_model"] = "model-v2"
    second = asyncio.run(service.process(ChatRequest(message="hello again", session_id=first.session_id)))

    assert first.content == "model-v1"
    assert second.content == "model-v2"
    assert factory.create_calls == 2


def test_agent_factory_prefers_google_provider_for_google_vendor_model_when_gemini_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"agent_model": "google/gemini-3.1-flash-lite-preview"})
    factory = AgentFactory(settings=Settings(agent_model="test"))

    model = factory._build_model(object, deps=deps)

    assert model == "google-gla:gemini-3.1-flash-lite-preview"


def test_agent_factory_redirects_legacy_openrouter_google_model_when_openrouter_key_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"agent_model": "openrouter:google/gemini-3.1-flash-lite-preview"})
    factory = AgentFactory(settings=Settings(agent_model="test"))

    model = factory._build_model(object, deps=deps)

    assert model == "google-gla:gemini-3.1-flash-lite-preview"


def test_agent_factory_prefers_openrouter_model_when_openrouter_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"agent_model": "openrouter:google/gemini-3.1-flash-lite-preview"})
    factory = AgentFactory(settings=Settings(agent_model="test"))

    model = factory._build_model(object, deps=deps)

    assert model == "openrouter:google/gemini-3.1-flash-lite-preview"


def test_app_lifespan_injects_runtime_state():
    app = create_app()

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert hasattr(app.state, "runtime_deps")
        assert hasattr(app.state, "agent_service")
        assert hasattr(app.state, "db_session_factory")


def test_chat_request_rejects_unsafe_session_id():
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", session_id="../../etc/passwd")


def test_agent_loop_service_routes_can_bus_question_to_general_chat(tmp_path):
    deps = build_test_deps(tmp_path)

    def general_chat_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        assert prompt_text == "CAN 总线电阻正常是多少"
        return ModelResponse(parts=[TextPart(content="CAN 总线两端并联后一般约 60 欧。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(general_chat_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="CAN 总线电阻正常是多少")))

    assert response.type == "message"
    assert response.content == "CAN 总线两端并联后一般约 60 欧。"
    assert response.business == "GENERAL_CHAT"


def test_agent_loop_service_respects_llm_intent_for_how_to_find_data_query(tmp_path, monkeypatch):
    deps = build_test_deps(tmp_path)
    monkeypatch.setattr(AgentLoopService, "_build_repair_knowledge_service", lambda self, runtime_deps: None)

    async def fake_route_async(self, message: str, mode: str | None = None):
        assert message == "怎样才能找到EDC17C53 P924 云内发动机电脑版数据"
        return IntentDecision(
            intent=RoutedIntent.GENERAL_CHAT,
            reason="方法咨询",
            source="llm",
            confidence=0.9,
        )

    monkeypatch.setattr("app.agent.runtime.intent_router.RequestIntentRouter.route_async", fake_route_async)

    def general_chat_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        assert prompt_text == "怎样才能找到EDC17C53 P924 云内发动机电脑版数据"
        return ModelResponse(parts=[TextPart(content="可以先从车型、ECU 型号和资料来源渠道三步去找。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(general_chat_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="怎样才能找到EDC17C53 P924 云内发动机电脑版数据")))

    assert response.type == "message"
    assert response.business == "GENERAL_CHAT"
    assert response.content == "可以先从车型、ECU 型号和资料来源渠道三步去找。"


def test_agent_loop_stream_done_uses_sanitized_full_content(tmp_path):
    deps = build_test_deps(tmp_path)

    async def general_stream(messages: list[ModelMessage], _: AgentInfo):
        prompt_text = extract_request_text(messages)
        assert prompt_text == "J1939 通讯故障怎么排查"
        yield "由于缺乏针对性的维修案例，建议您先检查 J1939 主干线终端电阻。\n"
        yield "如果问题仍然无法解决，为了提供更精确的排查路径，请补充您的车辆信息：\n车辆品牌及发动机型号"

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(stream_function=general_stream),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="J1939 通讯故障怎么排查")))

    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "message"
    assert done_event.metadata["full_content"] == "建议您先检查 J1939 主干线终端电阻。"


def test_agent_loop_service_falls_back_to_llm_for_fault_code_when_diagnosis_disabled(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": False})
    deps.fault_code_parser = get_fault_code_parser()

    def fault_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        assert "[FAULT_DIAGNOSIS_FALLBACK]" in prompt_text
        assert "已识别故障码：P0101" in prompt_text
        assert prompt_text.endswith("P0101")
        return ModelResponse(parts=[TextPart(content="P0101 常见于进气流量相关异常。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(fault_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="P0101")))

    assert response.type == "message"
    assert response.content == "P0101 常见于进气流量相关异常。"
    assert response.business == "FAULT_DIAGNOSIS"


def test_agent_loop_service_does_not_force_fault_fallback_when_general_chat_is_explicit(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": False})
    deps.fault_code_parser = get_fault_code_parser()

    def general_chat_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        assert prompt_text == "P0101"
        return ModelResponse(parts=[TextPart(content="按普通聊天处理。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(general_chat_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="P0101", mode="general_chat")))

    assert response.type == "message"
    assert response.content == "按普通聊天处理。"
    assert response.business == "GENERAL_CHAT"


def test_agent_loop_service_allows_query_parameters_as_internal_tool_in_general_chat(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.parameter_query_service = FakeParameterQueryService()

    def general_chat_with_param_tool(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            prompt_text = extract_request_text(messages)
            assert prompt_text == "EDC17C53 点火开关没反应，我下一步该怎么排查？"
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "EDC17C53 的 K46 是什么作用"},
                        tool_call_id="param_tool_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        assert tool_return.tool_name == "query_parameters"
        assert tool_return.content["data"]["rows"][0]["requested_value"] == "信号"
        return ModelResponse(parts=[TextPart(content="K46 是点火开关 T15 信号，可以继续检查点火开关输入是否正常。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(general_chat_with_param_tool),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="EDC17C53 点火开关没反应，我下一步该怎么排查？")))

    assert response.type == "message"
    assert response.business == "GENERAL_CHAT"
    assert "点火开关 T15 信号" in response.content


def test_agent_loop_service_resumes_internal_parameter_query_clarify_through_main_agent(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.parameter_query_service = FakeParameterQueryClarifyService()

    model_call_count = 0

    def general_chat_with_param_clarify(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        nonlocal model_call_count
        model_call_count += 1

        if len(messages) == 1:
            prompt_text = extract_request_text(messages)
            assert prompt_text == "APP1与APP2信号冲突，帮我看下怎么诊断"
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "APP1 开路电压多少"},
                        tool_call_id="param_tool_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "ask_user_question":
            assert tool_return.content["selection_payload"] == {
                "filters": {"param_source_id": "159", "param_field": "open_voltage"},
                "file_ids": [],
            }
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {
                            "query": "APP1 开路电压多少",
                            "selection_payload": tool_return.content["selection_payload"],
                        },
                        tool_call_id="param_tool_2",
                    )
                ]
            )

        assert tool_return.tool_name == "query_parameters"
        assert tool_return.content["data"]["rows"][0]["requested_value"] == "0V"
        return ModelResponse(
            parts=[TextPart(content="油门踏板1 开路电压 0V，可继续判断 APP1 线路开路或传感器供电异常。")]
        )

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(general_chat_with_param_clarify),
        ),
    )

    first = asyncio.run(service.process(ChatRequest(message="APP1与APP2信号冲突，帮我看下怎么诊断")))

    assert first.type == "ask_user"
    assert first.business == "GENERAL_CHAT"
    assert first.ask_user is not None
    saved_state = deps.deferred_state_store.load(first.session_id, first.ask_user.tool_call_id)
    assert saved_state is not None
    assert saved_state.tool_name == "ask_user_question"

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                message="",
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer="易控F17针脚电压",
                    metadata={
                        "selection_payload": {
                            "filters": {"param_source_id": "159", "param_field": "open_voltage"},
                            "file_ids": [],
                        }
                    },
                ),
            )
        )
    )

    assert second.type == "message"
    assert second.business == "GENERAL_CHAT"
    assert "APP1 线路开路" in second.content
    assert model_call_count == 3


def test_agent_loop_service_resets_history_after_user_confirms_switch(tmp_path):
    deps = build_test_deps(tmp_path)
    seen_message_counts: list[int] = []

    def switching_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        seen_message_counts.append(len(messages))
        prompt_text = extract_request_text(messages)
        if "先记录一下当前问题" in prompt_text:
            return ModelResponse(parts=[TextPart(content="先记录当前问题")])
        assert "P01F5 怎么办" in prompt_text
        return ModelResponse(parts=[TextPart(content="已切换到新的故障诊断问题")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(switching_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="先记录一下当前问题", session_id="switch-test")))
    second = asyncio.run(
        service.process(
            ChatRequest(
                message="P01F5 怎么办",
                session_id="switch-test",
                lifecycle_check={
                    "current_lifecycle": "ongoing",
                    "current_business": "GENERAL_CHAT",
                    "has_ongoing": True,
                    "user_confirmed_switch": True,
                },
            )
        )
    )

    assert first.content == "先记录当前问题"
    assert second.content == "已切换到新的故障诊断问题"
    assert seen_message_counts == [1, 1]


def test_agent_loop_service_blocks_external_tool_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "loop_guard_max_tool_calls", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_external_tool_calls", 1, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_tool_repeat", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_args_repeat", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_no_gain_streak", 6, raising=False)

    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": True})
    deps.fault_code_parser = get_fault_code_parser()

    class FakeDiagnosisClient:
        def __init__(self):
            self.calls: list[str] = []

        async def get_ecus_by_fault_code(self, fault_code: str):
            self.calls.append(fault_code)
            return SimpleNamespace(
                success=True,
                error=None,
                ecu_models=["EDC17CV44"],
                count=1,
            )

    fake_client = FakeDiagnosisClient()
    deps.diagnosis_client = fake_client

    def looping_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        repeat_index = 1 + sum(
            1
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "lookup_ecu_candidates"
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup_ecu_candidates",
                    {"fault_code": f"P010{repeat_index}"},
                    tool_call_id=f"lookup_ecu_{repeat_index}",
                )
            ]
        )

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(looping_llm),
        ),
    )

    response = asyncio.run(service.process(ChatRequest(message="P0101")))

    assert response.type == "message"
    assert response.business == "FAULT_DIAGNOSIS"
    assert response.metadata["convergence_reason"] == "loop_guard"
    assert response.metadata["convergence_mode"] == "best_effort_answer"
    assert response.metadata["guard_error_code"] == "LOOP_GUARD_MAX_EXTERNAL_TOOL_CALLS"
    assert "命中唯一 ECU" in response.content
    assert fake_client.calls == ["P0101"]


def test_agent_loop_service_converges_to_ask_user_from_fault_diag_clarify(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "loop_guard_max_tool_calls", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_external_tool_calls", 1, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_ask_user_calls", 2, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_tool_repeat", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_args_repeat", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_no_gain_streak", 6, raising=False)

    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": True})
    deps.fault_code_parser = get_fault_code_parser()

    class FakeDiagnosisClient:
        async def get_ecus_by_fault_code(self, fault_code: str):
            return SimpleNamespace(
                success=True,
                error=None,
                ecu_models=["EDC17CV44", "EDC7UC31"],
                count=2,
            )

    deps.diagnosis_client = FakeDiagnosisClient()

    def looping_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_ecu_candidates",
                        {"fault_code": "P0101"},
                        tool_call_id="fault_lookup_1",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "dtc_diagnosis",
                    {"fault_code": "P0101", "ecu_model": "EDC17CV44"},
                    tool_call_id="fault_diag_1",
                )
            ]
        )

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(looping_llm),
        ),
    )

    response = asyncio.run(service.process(ChatRequest(message="P0101")))

    assert response.type == "ask_user"
    assert response.business == "FAULT_DIAGNOSIS"
    assert response.metadata["convergence_reason"] == "loop_guard"
    assert response.metadata["convergence_mode"] == "ask_user_required"
    assert response.metadata["guard_error_code"] == "LOOP_GUARD_MAX_EXTERNAL_TOOL_CALLS"
    assert response.ask_user is not None
    assert response.ask_user.question == "识别到故障码 P0101，请选择对应 ECU："
    assert len(response.ask_user.options) == 2


def test_agent_loop_service_fault_diag_blocks_blind_guess_after_ambiguous_lookup(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": True})
    deps.fault_code_parser = get_fault_code_parser()

    class FakeDiagnosisClient:
        def __init__(self):
            self.lookup_calls: list[str] = []
            self.diagnosis_calls: list[tuple[str, str]] = []

        async def get_ecus_by_fault_code(self, fault_code: str):
            self.lookup_calls.append(fault_code)
            return SimpleNamespace(
                success=True,
                error=None,
                ecu_models=["EDC17CV44", "EDC7UC31"],
                count=2,
            )

        async def ensure_latest(self, ecu_model: str, fault_code: str, show_back: bool = True):
            del show_back
            self.diagnosis_calls.append((fault_code, ecu_model))
            return SimpleNamespace(
                success=True,
                state="ready",
                report_url=f"https://diag.example/{fault_code}/{ecu_model}",
                task_id="task_1",
                subscribe_url=None,
                report_id=1,
                error=None,
            )

    fake_client = FakeDiagnosisClient()
    deps.diagnosis_client = fake_client

    def blind_guess_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        last_tool_name = next(
            (
                part.tool_name
                for message in reversed(messages)
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ),
            None,
        )
        if last_tool_name is None:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_ecu_candidates",
                        {"fault_code": "P0101"},
                        tool_call_id="fault_lookup_1",
                    )
                ]
            )
        if last_tool_name == "lookup_ecu_candidates":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "dtc_diagnosis",
                        {"fault_code": "P0101", "ecu_model": "EDC17CV44"},
                        tool_call_id="fault_diag_1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="先按 EDC17CV44 继续诊断。")])

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(blind_guess_llm),
        ),
    )

    response = asyncio.run(service.process(ChatRequest(message="P0101")))

    assert response.type == "ask_user"
    assert response.business == "FAULT_DIAGNOSIS"
    assert response.ask_user is not None
    assert response.ask_user.question == "识别到故障码 P0101，请选择对应 ECU："
    assert fake_client.lookup_calls == ["P0101"]
    assert fake_client.diagnosis_calls == []


def test_agent_loop_service_fault_diag_reuses_failed_diagnosis_result_in_same_run(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": True})
    deps.fault_code_parser = get_fault_code_parser()

    class FakeDiagnosisClient:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def ensure_latest(self, ecu_model: str, fault_code: str, show_back: bool = True):
            del show_back
            self.calls.append((fault_code, ecu_model))
            return SimpleNamespace(
                success=False,
                state="failed",
                report_url=None,
                task_id=None,
                subscribe_url=None,
                report_id=None,
                error={"code": "DIAGNOSIS_FAILED", "message": "诊断服务暂时不可用"},
            )

    fake_client = FakeDiagnosisClient()
    deps.diagnosis_client = fake_client

    def repeated_failed_diag_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        diag_returns = sum(
            1
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "dtc_diagnosis"
        )
        if diag_returns == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "dtc_diagnosis",
                        {"fault_code": "P0101", "ecu_model": "EDC17CV44"},
                        tool_call_id="fault_diag_1",
                    )
                ]
            )
        if diag_returns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "dtc_diagnosis",
                        {"fault_code": "P0101", "ecu_model": "EDC17CV44"},
                        tool_call_id="fault_diag_2",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="我再试一次。")])

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(repeated_failed_diag_llm),
        ),
    )

    response = asyncio.run(service.process(ChatRequest(message="P0101 EDC17CV44")))

    assert response.type == "message"
    assert response.business == "FAULT_DIAGNOSIS"
    assert "诊断服务暂时不可用" in response.content
    assert fake_client.calls == [("P0101", "EDC17CV44")]


def test_agent_loop_stream_converges_instead_of_error_on_guard_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "loop_guard_max_tool_calls", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_external_tool_calls", 1, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_ask_user_calls", 2, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_tool_repeat", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_args_repeat", 6, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_no_gain_streak", 6, raising=False)

    deps = build_test_deps(tmp_path)
    deps.config_service = FakeConfigService({"diagnosis_service_enabled": True})
    deps.fault_code_parser = get_fault_code_parser()

    class FakeDiagnosisClient:
        async def get_ecus_by_fault_code(self, fault_code: str):
            return SimpleNamespace(
                success=True,
                error=None,
                ecu_models=["EDC17CV44", "EDC7UC31"],
                count=2,
            )

    deps.diagnosis_client = FakeDiagnosisClient()

    def looping_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_ecu_candidates",
                        {"fault_code": "P0101"},
                        tool_call_id="fault_lookup_1",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "dtc_diagnosis",
                    {"fault_code": "P0101", "ecu_model": "EDC17CV44"},
                    tool_call_id="fault_diag_1",
                )
            ]
        )

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(looping_llm),
        ),
    )

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="P0101")))

    assert not any(event.type == "error" for event in events)
    done_event = next(event for event in events if event.type == "done")
    assert done_event.metadata["convergence_mode"] == "ask_user_required"
    assert done_event.metadata["response"]["type"] == "ask_user"
