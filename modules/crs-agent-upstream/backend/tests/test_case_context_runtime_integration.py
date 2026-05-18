import asyncio

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.context import CaseContextManager, CaseContextStore
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings, settings
from app.legacy.services.clarify_service import ClarifyDecision
from app.schemas.chat import AskUserAnswer, ChatRequest


class FakeSession:
    def close(self):
        return None


class FakeExistenceResult:
    status = "exact_match"
    query_entities = {}
    matched_entities = {}
    unmatched_entities = {}
    suggestions = {}
    message = None
    should_continue = True


class FakeExistenceValidator:
    def validate(self, results, preprocessing):
        return FakeExistenceResult()


class FakeHardConstraintResult:
    ok = True
    missing_tokens = []
    checked_tokens = []
    message = None


class FakeHardConstraintValidator:
    def validate(self, results, preprocessing):
        return FakeHardConstraintResult()


class FakeClarifyService:
    def _get_facet_raw_value(self, result, facet):
        return result.get(facet)

    def _expand_emissions_raw_value(self, value):
        return value

    def _match_choice(self, value, choice):
        if not value:
            return False
        return str(choice).lower() in str(value).lower()

    def _match_emissions_choice(self, value, choice):
        return self._match_choice(value, choice)

    def analyze(self, results, preprocessing=None, existing_filters=None, clarify_round=0):
        del preprocessing, existing_filters, clarify_round
        if len(results) > 5:
            return ClarifyDecision(
                need=True,
                facet="series",
                question="请选择车型系列：",
                options=["天锦", "天龙"],
                reason="too_many_results",
            )
        return ClarifyDecision(need=False, facet=None, question=None, options=[], reason=None)


class FakeAmbiguousSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        del query, top_k, lexical_top_k
        assert use_vector is False
        return {
            "query": "帮我找东风电路图",
            "results": [
                {
                    "file_id": "1",
                    "filename": "东风天锦整车电路图A",
                    "physical_path": "/docs/1.pdf",
                    "pic_folder_url": "https://example.com/1",
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.45,
                },
                {
                    "file_id": "2",
                    "filename": "东风天龙整车电路图A",
                    "physical_path": "/docs/2.pdf",
                    "pic_folder_url": "https://example.com/2",
                    "brand": "东风",
                    "series": "天龙",
                    "score": 0.41,
                },
                {
                    "file_id": "3",
                    "filename": "东风天锦整车电路图B",
                    "physical_path": "/docs/3.pdf",
                    "pic_folder_url": "https://example.com/3",
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.39,
                },
                {
                    "file_id": "4",
                    "filename": "东风天龙整车电路图B",
                    "physical_path": "/docs/4.pdf",
                    "pic_folder_url": "https://example.com/4",
                    "brand": "东风",
                    "series": "天龙",
                    "score": 0.37,
                },
                {
                    "file_id": "5",
                    "filename": "东风天锦整车电路图C",
                    "physical_path": "/docs/5.pdf",
                    "pic_folder_url": "https://example.com/5",
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.35,
                },
                {
                    "file_id": "6",
                    "filename": "东风天龙整车电路图C",
                    "physical_path": "/docs/6.pdf",
                    "pic_folder_url": "https://example.com/6",
                    "brand": "东风",
                    "series": "天龙",
                    "score": 0.33,
                },
            ],
            "preprocessing": {"entities": {"brand": ["东风"]}},
            "search_method": "lexical_only",
            "search_time_ms": 18.0,
        }


class RecordingParameterQueryService:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def query(self, query: str, selection_payload=None, raw_query=None):
        self.calls.append({"query": query, "selection_payload": selection_payload, "raw_query": raw_query})
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


class NoGainParameterQueryService:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def query(self, query: str, selection_payload=None, raw_query=None):
        self.calls.append({"query": query, "selection_payload": selection_payload, "raw_query": raw_query})
        return {
            "status": "failed",
            "data": {"message": "参数资料暂不可用。"},
        }

    async def query_async(self, query: str, selection_payload=None, raw_query=None):
        return self.query(query, selection_payload=selection_payload, raw_query=raw_query)


def build_runtime_deps(tmp_path, parameter_query_service=None) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        case_context_store=CaseContextStore(base_dir=str(tmp_path / "case_context")),
        db_session_factory=lambda: FakeSession(),
        search_engine_factory=FakeAmbiguousSearchEngine,
        clarify_service=FakeClarifyService(),
        existence_validator=FakeExistenceValidator(),
        hard_constraint_validator=FakeHardConstraintValidator(),
        parameter_query_service=parameter_query_service,
    )


def build_unreachable_factory() -> AgentFactory:
    def llm(_messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        raise AssertionError("doc_search workflow should not call the LLM")

    return AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )


def extract_request_text(messages: list[ModelMessage]) -> str:
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    return "\n".join(
        part.content
        for part in request.parts
        if isinstance(getattr(part, "content", None), str)
    )


def seed_doc_search_context(deps: AgentRuntimeDeps) -> str:
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())
    first = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))
    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer="天锦",
                    metadata={"selection_payload": first.clarify_options[0].selection_payload},
                ),
            )
        )
    )
    assert second.type == "documents"
    return first.session_id


def test_case_context_is_reused_between_doc_search_and_parameter_query(tmp_path):
    parameter_service = RecordingParameterQueryService()
    deps = build_runtime_deps(tmp_path, parameter_query_service=parameter_service)
    session_id = seed_doc_search_context(deps)

    manager = CaseContextManager(store=deps.case_context_store)
    seeded = manager.load(session_id)
    assert seeded.slots.brand == "东风"
    assert seeded.slots.series == "天锦"

    def param_query_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            prompt_text = extract_request_text(messages)
            assert "[CASE_CONTEXT]" in prompt_text
            assert "品牌: 东风" in prompt_text
            assert "车系: 天锦" in prompt_text
            return ModelResponse(
                parts=[ToolCallPart("query_parameters", {"query": "K46 是什么作用"}, tool_call_id="param_query_1")]
            )
        return ModelResponse(parts=[TextPart(content="参数已读取。")])

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(param_query_llm),
        ),
    )

    response = asyncio.run(
        service.process(
            ChatRequest(
                session_id=session_id,
                message="K46 是什么作用",
                mode="param_query",
            )
        )
    )

    assert response.type == "param_request"
    assert parameter_service.calls[0]["query"] == "东风 天锦 K46 是什么作用"
    assert parameter_service.calls[0]["selection_payload"] == {
        "filters": {"brand": "东风", "series": "天锦"},
        "file_ids": ["1", "3", "5"],
    }

    updated = manager.load(session_id)
    assert updated.slots.parameter_source_id == "159"
    assert updated.slots.ecu_model == "EDC17C53"
    assert updated.task_type == "PARAM_QUERY"
    assert updated.answer_ready is True
    assert updated.attempted_actions[-1].action == "query_parameters"
    assert updated.attempted_actions[-1].info_gain == "medium"


def test_case_context_resets_after_lifecycle_switch(tmp_path):
    deps = build_runtime_deps(tmp_path, parameter_query_service=RecordingParameterQueryService())
    session_id = seed_doc_search_context(deps)

    def general_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        prompt_text = extract_request_text(messages)
        assert "[CASE_CONTEXT]" not in prompt_text
        assert prompt_text == "换个问题"
        return ModelResponse(parts=[TextPart(content="新问题已开始。")])

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(general_llm),
        ),
    )

    response = asyncio.run(
        service.process(
            ChatRequest(
                session_id=session_id,
                message="换个问题",
                lifecycle_check={"user_confirmed_switch": True, "current_business": "DOC_SEARCH", "has_ongoing": True},
            )
        )
    )

    assert response.type == "message"
    manager = CaseContextManager(store=deps.case_context_store)
    reset_context = manager.load(session_id)
    assert reset_context.artifacts == []
    assert reset_context.slots.brand is None
    assert reset_context.slots.series is None


def test_agent_loop_returns_error_when_loop_guard_exceeds_same_args(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "loop_guard_max_tool_calls", 8, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_tool_repeat", 5, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_args_repeat", 1, raising=False)

    parameter_service = RecordingParameterQueryService()
    deps = build_runtime_deps(tmp_path, parameter_query_service=parameter_service)

    def looping_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        repeat_index = 1 + sum(
            1
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "query_parameters"
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "query_parameters",
                    {"query": "K46 是什么作用"},
                    tool_call_id=f"param_query_{repeat_index}",
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

    response = asyncio.run(service.process(ChatRequest(message="K46 是什么作用", mode="param_query")))

    assert response.type == "param_request"
    assert response.metadata["runtime"] == "pydantic_ai"
    assert len(parameter_service.calls) == 1


def test_agent_loop_returns_error_when_loop_guard_exceeds_no_gain_streak(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "loop_guard_max_tool_calls", 8, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_external_tool_calls", 4, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_ask_user_calls", 2, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_tool_repeat", 5, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_same_args_repeat", 5, raising=False)
    monkeypatch.setattr(settings, "loop_guard_max_no_gain_streak", 1, raising=False)

    parameter_service = NoGainParameterQueryService()
    deps = build_runtime_deps(tmp_path, parameter_query_service=parameter_service)

    def looping_no_gain_llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        repeat_index = 1 + sum(
            1
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "query_parameters"
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "query_parameters",
                    {"query": f"K4{repeat_index} 是什么作用"},
                    tool_call_id=f"param_query_{repeat_index}",
                )
            ]
        )

    service = AgentLoopService(
        deps=deps,
        factory=AgentFactory(
            settings=Settings(agent_model="test"),
            model_override=FunctionModel(looping_no_gain_llm),
        ),
    )

    response = asyncio.run(service.process(ChatRequest(message="K46 是什么作用", mode="param_query")))

    assert response.type == "error"
    assert response.business == "AGENT_LOOP"
    assert response.content["error_code"] == "PARAM_QUERY_FAILED"
    assert "参数资料暂不可用" in response.content["message"]
    assert len(parameter_service.calls) == 1


def test_doc_search_query_is_enhanced_by_image_evidence(tmp_path):
    deps = build_runtime_deps(tmp_path)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())

    response = asyncio.run(
        service.process(
            ChatRequest(
                message="找电路图",
                context={
                    "image_evidence": {
                        "image_evidence_id": "img_doc_1",
                        "scene": "vehicle_identity",
                        "summary": "识别到东风天锦 KR 国六车辆信息。",
                        "vehicle": {
                            "brand": "东风",
                            "series": "天锦",
                            "model": "KR",
                            "engine": "DDi75E350-60",
                            "emission": "国六",
                        },
                        "diagnosis": {},
                        "suggested_queries": ["东风天锦 KR 国六 电路图"],
                    }
                },
            )
        )
    )

    assert response.type == "ask_user"
    assert response.business == "DOC_SEARCH"
    assert "东风" in response.ask_user.context.get("query", "")
    assert "天锦" in response.ask_user.context.get("query", "")
    assert "国六" in response.ask_user.context.get("query", "")


def test_parameter_query_uses_image_evidence_slots(tmp_path):
    parameter_service = RecordingParameterQueryService()
    deps = build_runtime_deps(tmp_path, parameter_query_service=parameter_service)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())

    response = asyncio.run(
        service.process(
            ChatRequest(
                message="K46 是什么作用",
                mode="param_query",
                context={
                    "image_evidence": {
                        "image_evidence_id": "img_param_1",
                        "scene": "vehicle_identity",
                        "summary": "识别到东风天锦 KR 国六 DDi75E350-60。",
                        "vehicle": {
                            "brand": "东风",
                            "series": "天锦",
                            "model": "KR",
                            "engine": "DDi75E350-60",
                            "emission": "国六",
                        },
                        "diagnosis": {},
                    }
                },
            )
        )
    )

    assert response.type == "param_request"
    assert parameter_service.calls
    query = str(parameter_service.calls[0]["query"])
    assert "东风" in query
    assert "天锦" in query
    assert "DDi75E350-60" in query
    assert "国六" in query
