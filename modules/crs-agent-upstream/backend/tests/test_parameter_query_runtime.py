import asyncio

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.domain.parameter_query.response_adapter import ParameterQueryResponseAdapter
from app.agent.context import CaseContextStore
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings
from app.schemas.chat import AskUserAnswer, ChatRequest


class FakeParameterQueryService:
    def query(self, query: str, selection_payload=None, raw_query=None):
        filters = dict((selection_payload or {}).get("filters") or {})
        if "abc999" in query.lower():
            return {
                "status": "ok",
                "data": {
                    "matched": False,
                    "query": query,
                    "reason": "ecu_not_found",
                    "message": "本地参数资料库中暂无“ABC999”相关 ECU 资料。",
                    "selected_source": None,
                    "source_refs": [],
                },
            }
        if filters.get("param_source_id") == "159" or "edc17c53" in query.lower():
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
                        "label": "EDC17C53针脚电压(12V系统)",
                        "description": "12V · 2 条针脚",
                        "selection_payload": {
                            "filters": {
                                "param_source_id": "159",
                                "param_field": "pin_definition",
                            },
                            "file_ids": [],
                        },
                    }
                ],
            },
        }

    async def query_async(self, query: str, selection_payload=None, raw_query=None):
        return self.query(query, selection_payload=selection_payload, raw_query=raw_query)


class RecordingParameterQueryService(FakeParameterQueryService):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(self, query: str, selection_payload=None, raw_query=None):
        self.calls.append(
            {
                "query": query,
                "selection_payload": selection_payload,
            }
        )
        return super().query(query, selection_payload=selection_payload)

    async def query_async(self, query: str, selection_payload=None, raw_query=None):
        return self.query(query, selection_payload=selection_payload, raw_query=raw_query)


class PendingPinParameterQueryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(self, query: str, selection_payload=None, raw_query=None):
        filters = dict((selection_payload or {}).get("filters") or {})
        self.calls.append(
            {
                "query": query,
                "selection_payload": selection_payload,
                "raw_query": raw_query,
            }
        )
        if filters.get("param_source_id") == "10" and "1.19" in query:
            return {
                "status": "ok",
                "data": {
                    "matched": True,
                    "query": query,
                    "summary": "1.19 的针脚定义为 CAN4H。",
                    "requested_field": "pin_definition",
                    "requested_field_label": "针脚定义",
                    "selected_source": {
                        "id": "10",
                        "title": "WISE10A针脚电压(24V系统)",
                        "ecu_name": "WISE10A",
                        "system_voltage": 24,
                        "pin_doc_kind": "pin_voltage",
                    },
                    "rows": [
                        {
                            "id": "1019",
                            "row_no": 19,
                            "component_name": "CAN4",
                            "ecu_pin_no": "1.19",
                            "pin_definition": "CAN4H",
                            "requested_value": "CAN4H",
                        }
                    ],
                    "source_refs": [
                        {
                            "id": "10",
                            "title": "WISE10A针脚电压(24V系统)",
                            "relation": "primary",
                            "match_score": 1.0,
                        }
                    ],
                },
            }
        return {
            "status": "need_clarify",
            "data": {
                "matched": False,
                "query": query,
                "clarify_type": "row",
                "reason": "missing_target",
                "selected_source": {
                    "id": "10",
                    "title": "WISE10A针脚电压(24V系统)",
                    "ecu_name": "WISE10A",
                    "system_voltage": 24,
                    "pin_doc_kind": "pin_voltage",
                },
            },
            "clarify": {
                "source": "parameter_query",
                "question": "请补充要查的具体针脚，例如 1.19、1.20",
                "results_count": 0,
                "options": [],
                "context": {
                    "scene": "parameter_query",
                    "clarify_type": "row",
                    "query": query,
                    "source_id": "10",
                    "source_title": "WISE10A针脚电压(24V系统)",
                    "input_hint": "请按当前 ECU 的针脚格式输入，例如：1.19、1.20",
                    "pin_examples": ["1.19", "1.20"],
                },
            },
        }

    async def query_async(self, query: str, selection_payload=None, raw_query=None):
        return self.query(query, selection_payload=selection_payload, raw_query=raw_query)


def build_test_deps(tmp_path) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        case_context_store=CaseContextStore(base_dir=str(tmp_path / "case_context")),
        parameter_query_service=FakeParameterQueryService(),
    )


def test_agent_loop_service_returns_param_request_for_parameter_query(tmp_path):
    deps = build_test_deps(tmp_path)

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "EDC17C53 的 K46 是什么作用"},
                        tool_call_id="param_query_1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="K46 的定义是信号。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(param_query_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="EDC17C53 的 K46 是什么作用")))

    assert response.type == "param_request"
    assert response.business == "PARAM_QUERY"
    assert response.content["rows"][0]["requested_value"] == "信号"


def test_agent_loop_service_recovers_param_card_when_llm_skips_query_tool(tmp_path):
    deps = build_test_deps(tmp_path)

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(
            lambda _messages, _info: ModelResponse(parts=[TextPart(content="66 针脚电压我直接告诉你。")])
        ),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="EDC17C53 的 K46 静态电压是多少")))

    assert response.type == "param_request"
    assert response.business == "PARAM_QUERY"
    assert response.content["selected_source"]["id"] == "159"
    assert response.content["rows"][0]["requested_value"] == "信号"


def test_agent_loop_service_routes_first_turn_param_query_without_main_agent(tmp_path):
    deps = build_test_deps(tmp_path)

    def should_not_run(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise AssertionError("first-turn param query should not rely on main agent")

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(should_not_run),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="K46 是什么作用")))

    assert response.type == "ask_user"
    assert response.business == "PARAM_QUERY"


def test_agent_loop_service_resumes_parameter_query_clarify(tmp_path):
    deps = build_test_deps(tmp_path)

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "K46 是什么作用"},
                        tool_call_id="param_query_1",
                    )
                ]
            )
        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        if tool_return.tool_name == "query_parameters":
            payload = tool_return.content["data"]
            if payload.get("matched") is True:
                assert payload["selected_source"]["id"] == "159"
                return ModelResponse(parts=[TextPart(content="已继续查询。")])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "question": "请先确认 ECU 型号",
                            "input_type": "single_select",
                            "options": [
                                {
                                    "key": "159",
                                    "label": "EDC17C53针脚电压(12V系统)",
                                    "description": "12V · 2 条针脚",
                                    "selection_payload": {
                                        "filters": {"param_source_id": "159", "param_field": "pin_definition"},
                                        "file_ids": [],
                                    },
                                }
                            ],
                            "allow_free_input": True,
                            "input_hint": "也可以直接输入 ECU 型号",
                        },
                        tool_call_id="ask_user_1",
                    )
                ]
            )
        assert tool_return.tool_name == "ask_user_question"
        assert tool_return.content["selection_payload"] == {
            "filters": {"param_source_id": "159", "param_field": "pin_definition"},
            "file_ids": [],
        }
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "query_parameters",
                    {
                        "query": "K46 是什么作用",
                        "selection_payload": tool_return.content["selection_payload"],
                    },
                    tool_call_id="param_query_2",
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(param_query_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="K46 是什么作用")))

    assert first.type == "ask_user"
    assert first.business == "PARAM_QUERY"
    assert first.ask_user is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                message="",
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer="EDC17C53针脚电压(12V系统)",
                    metadata={
                        "selection_payload": {
                            "filters": {"param_source_id": "159", "param_field": "pin_definition"},
                            "file_ids": [],
                        }
                    },
                ),
            )
        )
    )

    assert second.type == "param_request"
    assert second.business == "PARAM_QUERY"
    assert second.content["selected_source"]["id"] == "159"


def test_agent_loop_service_parameter_need_clarify_defers_without_model_followup(tmp_path):
    deps = build_test_deps(tmp_path)

    call_count = 0

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise AssertionError("parameter clarify should defer before the model enters a second reasoning step")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "query_parameters",
                    {"query": "K46 是什么作用"},
                    tool_call_id="param_query_1",
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(param_query_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="K46 是什么作用")))

    assert response.type == "ask_user"
    assert response.business == "PARAM_QUERY"
    assert response.ask_user is not None
    assert response.ask_user.question == "请先确认 ECU 型号"
    assert call_count == 0


def test_parameter_query_adapter_uses_text_only_copy_when_no_candidate_options():
    ask_user = ParameterQueryResponseAdapter.build_ask_user_question(
        {
            "clarify": {
                "question": "请补充 ECU 型号",
                "options": [],
                "context": {
                    "scene": "parameter_query",
                    "clarify_type": "source",
                    "query": "0H6风扇离合器电压多少",
                },
            }
        }
    )

    form = ask_user.context["form"]
    field = form["sections"][0]["fields"][0]

    assert ask_user.question == "请补充 ECU / 控制器型号"
    assert form["description"] == "请直接补充 ECU / 控制器型号；如果暂时不清楚，也可以补充车型、发动机或系统信息。"
    assert form["ask_reason"] == "当前还无法从本地资料中唯一定位 ECU 或资料来源。"
    assert form["ui_policy"]["show_summary_preview"] is False
    assert form["ui_policy"]["dense"] is True
    assert field["field_type"] == "text"


def test_parameter_query_adapter_keeps_manual_input_visible_when_options_exist():
    ask_user = ParameterQueryResponseAdapter.build_ask_user_question(
        {
            "clarify": {
                "question": "请先确认 ECU 型号",
                "options": [
                    {
                        "key": "159",
                        "label": "EDC17C53",
                        "description": "12V · 2 条针脚",
                        "selection_payload": {
                            "filters": {
                                "param_source_id": "159",
                            },
                            "file_ids": [],
                        },
                    }
                ],
                "context": {
                    "scene": "parameter_query",
                    "clarify_type": "source",
                    "query": "0H6风扇离合器电压多少",
                },
            }
        }
    )

    field = ask_user.context["form"]["sections"][0]["fields"][0]

    assert field["field_type"] == "single_select"
    assert field["manual_input"]["enabled"] is True
    assert field["manual_input"]["always_visible"] is True


def test_parameter_query_adapter_uses_row_specific_copy_for_row_clarify():
    ask_user = ParameterQueryResponseAdapter.build_ask_user_question(
        {
            "clarify": {
                "question": "请确认要查的针脚",
                "options": [
                    {
                        "key": "14667",
                        "label": "AA2 / 风扇离合器 / 控制",
                        "description": "0V",
                        "selection_payload": {
                            "filters": {
                                "param_source_id": "6",
                                "param_row_id": "14667",
                                "param_field": "voltage",
                            },
                            "file_ids": [],
                        },
                    }
                ],
                "context": {
                    "scene": "parameter_query",
                    "clarify_type": "row",
                    "query": "OH6 风扇离合器电压多少",
                    "source_id": "6",
                    "source_title": "OH6针脚电压(24V系统)",
                },
            }
        }
    )

    form = ask_user.context["form"]

    assert form["description"] == "已定位到 ECU，优先点选最接近的针脚或部件；没有合适项再自行补充。"
    assert form["ask_reason"] == "当前还需要确认具体针脚或目标行。"


def test_parameter_query_adapter_uses_text_copy_for_missing_pin_target():
    ask_user = ParameterQueryResponseAdapter.build_ask_user_question(
        {
            "clarify": {
                "question": "请补充要查的具体针脚",
                "options": [],
                "context": {
                    "scene": "parameter_query",
                    "clarify_type": "row",
                    "query": "EDC17C53 的针脚定义是什么",
                    "source_id": "159",
                    "source_title": "EDC17C53针脚电压(12V系统)",
                    "message": "已识别 ECU 为 EDC17C53，但当前问题还缺少具体目标。请补充针脚编号、信号名称或零部件名称后再查询。",
                },
            }
        }
    )

    form = ask_user.context["form"]

    assert ask_user.question == "请补充要查的具体针脚"
    assert ask_user.input_type.value == "text"
    assert form["description"] == "已定位到 ECU，请直接补充更准确的针脚编号、信号名称或部件名称。"
    assert form["ask_reason"] == "已识别 ECU 为 EDC17C53，但当前问题还缺少具体目标。请补充针脚编号、信号名称或零部件名称后再查询。"


def test_agent_loop_service_parameter_clarify_resume_does_not_reenter_agent(tmp_path):
    deps = build_test_deps(tmp_path)

    call_count = 0

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise AssertionError("parameter clarify resume should go through deterministic workflow")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "query_parameters",
                    {"query": "K46 是什么作用"},
                    tool_call_id="param_query_1",
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(param_query_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="K46 是什么作用")))
    assert first.type == "ask_user"
    assert first.ask_user is not None

    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer="EDC17C53针脚电压(12V系统)",
                    metadata={
                        "selection_payload": {
                            "filters": {"param_source_id": "159", "param_field": "pin_definition"},
                            "file_ids": [],
                        }
                    },
                ),
            )
        )
    )

    assert second.type == "param_request"
    assert second.business == "PARAM_QUERY"
    assert second.content["selected_source"]["id"] == "159"
    assert call_count == 0


def test_agent_loop_service_continues_pending_parameter_query_from_plain_text(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.parameter_query_service = PendingPinParameterQueryService()

    def should_not_enter_agent(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        raise AssertionError("pending parameter query should be resumed before entering repair/general agent")

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(should_not_enter_agent),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="WISE10A 的针脚定义是什么")))

    assert first.type == "ask_user"
    assert first.business == "PARAM_QUERY"
    assert first.ask_user is not None
    assert "1.19" in first.ask_user.question
    assert "1.19" in first.ask_user.input_hint

    second = asyncio.run(service.process(ChatRequest(session_id=first.session_id, message="1.19")))

    assert second.type == "param_request"
    assert second.business == "PARAM_QUERY"
    assert second.content["selected_source"]["id"] == "10"
    assert second.content["rows"][0]["ecu_pin_no"] == "1.19"
    assert second.content["rows"][0]["requested_value"] == "CAN4H"
    assert deps.parameter_query_service.calls[-1]["selection_payload"]["filters"]["param_source_id"] == "10"


def test_agent_loop_service_reuses_same_run_param_no_match_for_duplicate_probe(tmp_path):
    deps = build_test_deps(tmp_path)
    deps.parameter_query_service = RecordingParameterQueryService()

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        tool_returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "query_parameters"
        ]
        if not tool_returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "ABC999 K46 引脚是什么作用"},
                        tool_call_id="param_query_1",
                    )
                ]
            )
        if len(tool_returns) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "ABC999 的 K46 是什么作用"},
                        tool_call_id="param_query_2",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="结束。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(param_query_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="ABC999 K46 引脚是什么作用")))

    assert response.type == "message"
    assert response.business == "PARAM_QUERY"
    assert "ABC999" in response.content
    assert len(deps.parameter_query_service.calls) == 1


def test_agent_loop_service_returns_direct_message_when_param_query_no_match(tmp_path):
    deps = build_test_deps(tmp_path)

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "query_parameters",
                        {"query": "ABC999 K46 引脚是什么作用"},
                        tool_call_id="param_query_1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="模型本不该看到这个最终文案。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(param_query_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="ABC999 K46 引脚是什么作用")))

    assert response.type == "message"
    assert response.business == "PARAM_QUERY"
    assert "暂无" in response.content
