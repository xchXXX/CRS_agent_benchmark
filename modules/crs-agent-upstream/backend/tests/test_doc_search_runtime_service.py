import asyncio
import types

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app.agent.models.ask_user import AskUserInputType
from app.agent.models.events import AgentEventType
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.domain.doc_search.models import DocSearchPlannedQuery, DocSearchQueryPlan
from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings
from app.legacy.services.clarify_service import ClarifyDecision
from app.schemas.chat import AskUserAnswer, ChatRequest


class FakeSession:
    def close(self):
        return None


class FakeConfigService:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get(self, key, default=None):
        return self._values.get(key, default)


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


class FakeFailingHardConstraintResult:
    ok = False
    missing_tokens = ["J6P"]
    checked_tokens = ["J6P"]
    message = "抱歉，暂无相关资料在数据库中。"


class FakeFailingHardConstraintValidator:
    def validate(self, results, preprocessing):
        return FakeFailingHardConstraintResult()


class FakeClarifyService:
    def _get_facet_raw_value(self, result, facet):
        return result.get(facet)

    def _expand_emissions_raw_value(self, value):
        return value

    def _match_choice(self, value, choice):
        if not value:
            return False
        if isinstance(value, list):
            return any(str(choice).lower() in str(item).lower() for item in value)
        return str(choice).lower() in str(value).lower()

    def _match_emissions_choice(self, value, choice):
        return self._match_choice(value, choice)

    def analyze(self, results, preprocessing=None, existing_filters=None, clarify_round=0):
        if len(results) > 5:
            return ClarifyDecision(
                need=True,
                facet="series",
                question="请选择车型系列：",
                options=["天锦", "天龙"],
                reason="too_many_results",
            )
        return ClarifyDecision(need=False, facet=None, question=None, options=[], reason=None)


class FakeClarifyServiceWithOther(FakeClarifyService):
    def analyze(self, results, preprocessing=None, existing_filters=None, clarify_round=0):
        if len(results) > 5:
            return ClarifyDecision(
                need=True,
                facet="series",
                question="请选择车型系列：",
                options=["天锦", "天龙", "其他"],
                reason="too_many_results",
            )
        return ClarifyDecision(need=False, facet=None, question=None, options=[], reason=None)


class FakeAmbiguousSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {
                    "file_id": "1",
                    "filename": "东风天锦整车电路图A",
                    "physical_path": "/docs/1.pdf",
                    "pic_folder_url": "https://example.com/1",
                    "ggzj_data_type": 3,
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
                    "ggzj_data_type": 3,
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


class CountingOtherSearchEngine(FakeAmbiguousSearchEngine):
    calls = 0

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        type(self).calls += 1
        return super().search(query, top_k=top_k, lexical_top_k=lexical_top_k, use_vector=use_vector)


class FakePreciseSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {
                    "file_id": "1",
                    "filename": "东风天锦整车电路图A",
                    "physical_path": "/docs/1.pdf",
                    "pic_folder_url": "https://example.com/1",
                    "ggzj_data_type": 3,
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.45,
                },
                {
                    "file_id": "3",
                    "filename": "东风天锦整车电路图B",
                    "physical_path": "/docs/3.pdf",
                    "pic_folder_url": "https://example.com/3",
                    "ggzj_data_type": 3,
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.39,
                },
            ],
            "preprocessing": {"entities": {"brand": ["东风"], "series": ["天锦"]}},
            "search_method": "lexical_only",
            "search_time_ms": 12.0,
        }


class FakeExactTitleSearchEngine:
    TITLE = "青岛解放_中联自卸搅拌车_车身电线束图(3724045-DM853)【博世系统】"

    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {
                    "file_id": "exact_1",
                    "filename": self.TITLE,
                    "physical_path": "/docs/exact.pdf",
                    "pic_folder_url": "https://example.com/exact",
                    "brand": "青岛解放",
                    "series": "中联自卸搅拌车",
                    "score": 0.99,
                },
                {
                    "file_id": "near_1",
                    "filename": f"{self.TITLE}_补充说明",
                    "physical_path": "/docs/near1.pdf",
                    "pic_folder_url": "https://example.com/near1",
                    "brand": "青岛解放",
                    "series": "中联自卸搅拌车",
                    "score": 0.98,
                },
                {
                    "file_id": "near_2",
                    "filename": "青岛解放_中联自卸搅拌车_整车电路图",
                    "physical_path": "/docs/near2.pdf",
                    "pic_folder_url": "https://example.com/near2",
                    "brand": "青岛解放",
                    "series": "中联自卸搅拌车",
                    "score": 0.97,
                },
                {
                    "file_id": "near_3",
                    "filename": "青岛解放_中联自卸搅拌车_底盘电线束图",
                    "physical_path": "/docs/near3.pdf",
                    "pic_folder_url": "https://example.com/near3",
                    "brand": "青岛解放",
                    "series": "中联自卸搅拌车",
                    "score": 0.96,
                },
                {
                    "file_id": "near_4",
                    "filename": "青岛解放_中联自卸搅拌车_上装控制器线束图",
                    "physical_path": "/docs/near4.pdf",
                    "pic_folder_url": "https://example.com/near4",
                    "brand": "青岛解放",
                    "series": "中联自卸搅拌车",
                    "score": 0.95,
                },
                {
                    "file_id": "near_5",
                    "filename": "青岛解放_中联自卸搅拌车_车身电线束图",
                    "physical_path": "/docs/near5.pdf",
                    "pic_folder_url": "https://example.com/near5",
                    "brand": "青岛解放",
                    "series": "中联自卸搅拌车",
                    "score": 0.94,
                },
            ],
            "preprocessing": {"entities": {"brand": ["青岛解放"], "series": ["中联自卸搅拌车"]}},
            "search_method": "lexical_only",
            "search_time_ms": 12.0,
        }


class FakeCircuitBodySearchEnhancer:
    def __init__(self):
        self.calls = []

    async def enhance(
        self,
        *,
        results,
        body_keyword: str,
        max_docs: int = 3,
        candidate_query: str = "",
        max_candidate_docs: int = 20,
        trace_callback=None,
    ):
        self.calls.append(
            {
                "body_keyword": body_keyword,
                "max_docs": max_docs,
                "candidate_query": candidate_query,
                "max_candidate_docs": max_candidate_docs,
                "result_count": len(results),
            }
        )
        if trace_callback is not None:
            trace_callback(
                "circuit_body_search_completed",
                {
                    "keyword": body_keyword,
                    "candidate_query": candidate_query,
                    "source_result_count": len(results),
                    "final_result_count": len(results),
                    "doc_search_count": len(results),
                    "doc_hit_count": 1 if results else 0,
                    "doc_failed_count": 0,
                    "enhanced_existing_count": 1 if results else 0,
                    "inserted_candidate_hit_count": 0,
                },
                None,
            )
        enhanced = [dict(item) for item in results]
        if enhanced:
            enhanced[0]["body_search"] = {
                "status": "hit",
                "pdf_id": "pdf_1",
                "keyword": body_keyword,
                "best_hit": {"page_index": 0, "page_number": 1},
            }
        return enhanced


class FakeConflictSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {
                    "file_id": "9",
                    "filename": "东风通用电路图",
                    "physical_path": "/docs/9.pdf",
                    "pic_folder_url": "https://example.com/9",
                    "brand": "东风",
                    "series": "通用",
                    "score": 0.12,
                }
            ],
            "preprocessing": {"entities": {"brand": ["东风"], "series": ["J6P"]}},
            "search_method": "lexical_only",
            "search_time_ms": 9.0,
        }


class FakeConflictDimensionService:
    is_loaded = True

    def get_root_value_in_facet(self, facet, value):
        return value

    def get_parent(self, facet, value):
        return None

    def detect_conflicts(self, entities):
        if entities.get("brand") == ["东风"] and entities.get("series") == ["J6P"]:
            return [
                type(
                    "Conflict",
                    (),
                    {
                        "message": "检测到品牌和系列冲突，请选择正确的组合：",
                        "type": "brand_series_conflict",
                        "options": [
                            {
                                "key": "东风",
                                "label": "东风（保留品牌）",
                                "description": "按东风继续搜索",
                                "filters": {"brand": "东风"},
                            },
                            {
                                "key": "解放 J6P",
                                "label": "解放 J6P",
                                "description": "按解放 J6P 继续搜索",
                                "filters": {"brand": "解放", "series": "J6P"},
                            },
                        ],
                    },
                )()
            ]
        return []


def build_deps(
    tmp_path,
    search_engine_factory,
    *,
    clarify_service=None,
    dimension_service=None,
    hard_constraint_validator=None,
) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        db_session_factory=lambda: FakeSession(),
        search_engine_factory=search_engine_factory,
        clarify_service=clarify_service or FakeClarifyService(),
        config_service=FakeConfigService({"agent_model": "test", "openrouter_clarify_model": "test"}),
        existence_validator=FakeExistenceValidator(),
        hard_constraint_validator=hard_constraint_validator or FakeHardConstraintValidator(),
        dimension_service=dimension_service,
    )


def build_unreachable_factory() -> AgentFactory:
    def llm(_messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        raise AssertionError("doc_search workflow should not call the LLM")

    return AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )


async def collect_stream_events(service: AgentLoopService, request: ChatRequest):
    return [event async for event in service.stream(request)]


def test_runtime_returns_structured_clarify_for_ambiguous_doc_search(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))

    assert response.type == "ask_user"
    assert response.business == "DOC_SEARCH"
    assert response.ask_user is not None
    assert response.ask_user.question == "请选择车型系列："
    assert response.ask_user.input_type == AskUserInputType.SINGLE_SELECT
    assert [option.label for option in response.ask_user.options] == ["天锦", "天龙"]
    assert response.clarify_options[0].selection_payload == {
        "filters": {"brand": "东风", "series": "天锦"},
        "file_ids": [],
    }


def test_runtime_resumes_doc_search_ask_user_with_documents(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))
    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer={
                    "tool_call_id": first.ask_user.tool_call_id,
                    "answer": "天锦",
                    "metadata": {
                        "selection_payload": first.clarify_options[0].selection_payload,
                    },
                },
            )
        )
    )

    assert second.type == "documents"
    assert second.business == "DOC_SEARCH"
    assert second.content["total"] == 3
    assert second.content["returned_count"] == 3
    assert all(item["series"] == "天锦" for item in second.content["results"])


def test_runtime_resolves_doc_search_selection_payload_from_answer_text(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))
    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer="天锦",
                ),
            )
        )
    )

    assert second.type == "documents"
    assert second.business == "DOC_SEARCH"
    assert second.content["total"] == 3
    assert all(item["series"] == "天锦" for item in second.content["results"])
    assert not second.metadata.get("recovered_after_clarify_without_tool_call")


def test_runtime_resumes_doc_search_other_without_research(tmp_path):
    CountingOtherSearchEngine.calls = 0
    deps = build_deps(
        tmp_path,
        CountingOtherSearchEngine,
        clarify_service=FakeClarifyServiceWithOther(),
    )
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    first = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))
    other_option = next(option for option in first.clarify_options if option.label == "其他")
    assert other_option.selection_payload == {
        "filters": {"brand": "东风"},
        "file_ids": ["3", "4", "5", "6"],
    }
    second = asyncio.run(
        service.process(
            ChatRequest(
                session_id=first.session_id,
                ask_user_answer=AskUserAnswer(
                    tool_call_id=first.ask_user.tool_call_id,
                    answer="其他",
                    metadata={"selection_payload": other_option.selection_payload},
                ),
            )
        )
    )

    assert CountingOtherSearchEngine.calls == 1
    assert second.type == "documents"
    assert second.business == "DOC_SEARCH"
    assert second.content["total"] == 4
    assert [item["file_id"] for item in second.content["results"]] == ["3", "4", "5", "6"]


def test_runtime_converts_doc_search_summary_into_documents_response(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)

    def llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_documents",
                        {"query": "东风天锦电路图", "top_k": 20},
                        tool_call_id="search_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        assert tool_return.tool_name == "search_documents"
        return ModelResponse(parts=[TextPart(content="我已经找到文档了。")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="东风天锦电路图")))

    assert response.type == "documents"
    assert response.business == "DOC_SEARCH"
    assert response.content["total"] == 2
    assert response.content["results"][0]["filename"] == "东风天锦整车电路图A"


def test_runtime_ignores_model_output_validation_after_doc_search_split(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))

    assert response.type == "ask_user"
    assert response.business == "DOC_SEARCH"
    assert response.ask_user is not None
    assert response.ask_user.question == "请选择车型系列："


def test_runtime_ignores_model_authored_free_text_doc_search_question(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)

    def llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_documents",
                        {"query": "东风电路图", "top_k": 20},
                        tool_call_id="search_1",
                    )
                ]
            )

        last_request = messages[-1]
        assert isinstance(last_request, ModelRequest)
        tool_return = next(part for part in last_request.parts if isinstance(part, ToolReturnPart))
        assert tool_return.tool_name == "search_documents"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "question": "抱歉，找到的文档很多，请问您需要哪个具体型号或系列的东风电路图？例如：天锦、天龙、EQ150等",
                        "input_type": "text",
                        "options": [],
                        "allow_free_input": True,
                    },
                    tool_call_id="ask_user_1",
                )
            ]
        )

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))

    assert response.type == "ask_user"
    assert response.business == "DOC_SEARCH"
    assert response.ask_user is not None
    assert response.ask_user.question == "请选择车型系列："
    assert response.ask_user.input_type == AskUserInputType.SINGLE_SELECT
    assert response.ask_user.allow_free_input is False
    assert [option.label for option in response.ask_user.options] == ["天锦", "天龙"]


def test_runtime_returns_documents_for_precise_doc_search_without_llm(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="东风天锦电路图")))

    assert response.type == "documents"
    assert response.business == "DOC_SEARCH"
    assert response.content["total"] == 2
    assert all(item["series"] == "天锦" for item in response.content["results"])


def test_runtime_routes_doc_body_location_search_even_with_stale_general_chat_cache(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(
        service.process(
            ChatRequest(
                message="找东风天锦整车电路图里面BCM的位置",
                context={
                    "__resolved_request_intent": {
                        "intent": "general_chat",
                        "reason": "stale_frontend_cache",
                    }
                },
            )
        )
    )

    assert response.type == "documents"
    assert response.business == "DOC_SEARCH"
    assert response.content["total"] == 2


def test_runtime_enhances_precise_doc_search_with_circuit_body_search(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    enhancer = FakeCircuitBodySearchEnhancer()
    deps.circuit_body_search_enhancer = enhancer
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="东风天锦电路图")))

    assert response.type == "documents"
    assert enhancer.calls == [
        {
            "body_keyword": "东风天锦电路图",
            "max_docs": 2,
            "candidate_query": "东风天锦电路图",
            "max_candidate_docs": 20,
            "result_count": 2,
        }
    ]
    assert response.content["results"][0]["body_search"]["status"] == "hit"


def test_runtime_does_not_enhance_doc_search_when_clarify_is_needed(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)
    enhancer = FakeCircuitBodySearchEnhancer()
    deps.circuit_body_search_enhancer = enhancer
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))

    assert response.type == "ask_user"
    assert enhancer.calls == []


def test_runtime_returns_structured_message_for_invalid_doc_search(tmp_path):
    deps = build_deps(
        tmp_path,
        FakePreciseSearchEngine,
        hard_constraint_validator=FakeFailingHardConstraintValidator(),
    )

    def llm(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_documents",
                        {"query": "J6P 电路图", "top_k": 20},
                        tool_call_id="search_1",
                    )
                ]
            )

        return ModelResponse(parts=[TextPart(content="模型不应接管 invalidity 分支")])

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="J6P 电路图")))

    assert response.type == "message"
    assert response.business == "DOC_SEARCH"
    assert response.content["message"] == "抱歉，暂无相关资料在数据库中。"
    assert response.content["should_archive_previous"] is True


def test_runtime_prioritizes_conflict_clarify_before_invalidity(tmp_path):
    deps = build_deps(
        tmp_path,
        FakeConflictSearchEngine,
        dimension_service=FakeConflictDimensionService(),
        hard_constraint_validator=FakeFailingHardConstraintValidator(),
    )

    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="东风 J6P 电路图")))

    assert response.type == "ask_user"
    assert response.business == "DOC_SEARCH"
    assert response.ask_user is not None
    assert response.ask_user.question == "检测到品牌和系列冲突，请选择正确的组合："
    assert [option.label for option in response.ask_user.options] == ["东风（保留品牌）", "解放 J6P"]


def test_runtime_stream_returns_doc_search_cards_without_text_deltas(tmp_path):
    deps = build_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="帮我找东风电路图")))

    assert any(event.type == AgentEventType.START for event in events)
    assert any(event.type == AgentEventType.HINT for event in events)
    assert [event.content for event in events if event.type == AgentEventType.TEXT_DELTA] == []

    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "ask_user"
    assert done_event.metadata["response"]["business"] == "DOC_SEARCH"
    assert done_event.metadata["full_content"] == ""


def test_runtime_stream_emits_circuit_body_search_hint(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    deps.circuit_body_search_enhancer = FakeCircuitBodySearchEnhancer()
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(
        collect_stream_events(
            service,
            ChatRequest(message="找东风天锦整车电路图里面BCM的位置"),
        )
    )

    hints = [event.message or event.content for event in events if event.type == AgentEventType.HINT]
    assert "正在电路图内搜索定位，请稍候..." in hints
    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "documents"
    assert done_event.metadata["response"]["business"] == "DOC_SEARCH"


def test_runtime_stream_keeps_general_chat_text_deltas(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)

    async def stream_llm(_messages: list[ModelMessage], _: AgentInfo):
        yield "stream-"
        yield "ok"

    factory = AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(stream_function=stream_llm),
    )
    service = AgentLoopService(deps=deps, factory=factory)

    events = asyncio.run(collect_stream_events(service, ChatRequest(message="hello")))

    chunks = [event.content for event in events if event.type == AgentEventType.TEXT_DELTA]
    assert chunks == ["stream-", "ok"]

    done_event = next(event for event in events if event.type == AgentEventType.DONE)
    assert done_event.metadata["response"]["type"] == "message"
    assert done_event.metadata["response"]["business"] == "GENERAL_CHAT"
    assert done_event.metadata["full_content"] == "stream-ok"


def test_extract_doc_search_image_hint_queries_prioritizes_suggested_queries_and_numeric_codes():
    payloads = [
        {
            "image_evidence_id": "img_numeric_ecu",
            "scene": "document_hint",
            "summary": "疑似云内国方 ECU，标签包含 MDD01、22080203 和 H-RN217-1。",
            "vehicle": {"brand": "云内"},
            "visible_text": [
                "苏州国方汽车电子有限公司",
                "MDD01 / ECUA-00-000056",
                "22080203",
                "2204005131",
                "H-RN217-1",
            ],
            "suggested_queries": [
                "国方MDD01资料",
                "云内 MDD01 ECU电路图",
            ],
        }
    ]

    queries = AgentLoopService._extract_doc_search_image_hint_queries(payloads)

    assert queries[:2] == ("国方MDD01资料", "云内 MDD01 ECU电路图")
    assert any(query in queries for query in ("22080203", "22080203 电路图", "22080203 ECU电路图"))
    assert any("国方22080203" in query or "国方 22080203" in query for query in queries)
    assert "H-RN217-1" not in queries or queries.index("国方MDD01资料") < queries.index("H-RN217-1")


def test_plan_doc_search_queries_uses_image_hints_when_planner_unavailable(tmp_path):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        return None

    request = ChatRequest(
        message="这个板子资料是哪个，带计量单元2线的云内发动机",
        context={
            "image_evidences": [
                {
                    "image_evidence_id": "img_plan_fallback",
                    "scene": "document_hint",
                    "summary": "疑似国方 ECU 板卡",
                    "vehicle": {"brand": "云内"},
                    "visible_text": ["苏州国方汽车电子有限公司", "MDD01 / ECUA-00-000056", "22080203"],
                    "suggested_queries": ["国方MDD01资料", "云内 MDD01 ECU电路图"],
                }
            ]
        },
    )
    active_deps = service._prepare_request_runtime_deps(runtime_deps=deps, request=request, session_id="sess_plan_fallback")

    original_plan = PydanticAIDocSearchQueryPlanner.plan
    PydanticAIDocSearchQueryPlanner.plan = fake_plan
    try:
        primary_query, executed_queries, rationale, body_keyword = asyncio.run(
            service._plan_doc_search_queries(
                request=request,
                active_deps=active_deps,
                fallback_query=request.message,
            )
        )
    finally:
        PydanticAIDocSearchQueryPlanner.plan = original_plan

    assert rationale == ""
    assert body_keyword == ""
    assert primary_query == "这个板子资料是哪个 带计量单元2线的云内发动机"
    assert executed_queries[0].query == "这个板子资料是哪个 带计量单元2线的云内发动机"
    assert any(item.query == "国方MDD01资料" for item in executed_queries)
    assert any("22080203" in item.query for item in executed_queries)


def test_plan_doc_search_queries_uses_text_mode_planner_body_keyword(tmp_path, monkeypatch):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())
    calls = []

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        calls.append(
            {
                "query": query,
                "image_evidence": image_evidence,
                "known_slots": known_slots,
                "input_mode": input_mode,
            }
        )
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="东风天锦电路图",
            queries=[DocSearchPlannedQuery(query="东风天锦电路图", confidence=0.95)],
            body_keyword="涡轮增压器执行器",
            body_keyword_confidence=0.92,
            rationale="拆分资料名和图内定位词。",
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    request = ChatRequest(message="找东风天锦电路图里面涡轮增压器执行器")
    active_deps = service._prepare_request_runtime_deps(runtime_deps=deps, request=request, session_id="sess_text_plan")

    primary_query, executed_queries, rationale, body_keyword = asyncio.run(
        service._plan_doc_search_queries(
            request=request,
            active_deps=active_deps,
            fallback_query=request.message,
        )
    )

    assert calls == [
        {
            "query": "找东风天锦电路图里面涡轮增压器执行器",
            "image_evidence": "",
            "known_slots": "",
            "input_mode": "text",
        }
    ]
    assert primary_query == "东风天锦电路图"
    assert [item.query for item in executed_queries] == ["东风天锦电路图"]
    assert rationale == "拆分资料名和图内定位词。"
    assert body_keyword == "涡轮增压器执行器"


def test_plan_doc_search_queries_uses_text_image_mode(tmp_path, monkeypatch):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())
    calls = []

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        calls.append({"query": query, "image_evidence": image_evidence, "input_mode": input_mode})
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="云内 MDD01 ECU电路图",
            queries=[DocSearchPlannedQuery(query="云内 MDD01 ECU电路图", confidence=0.91)],
            body_keyword="计量单元",
            body_keyword_confidence=0.88,
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    request = ChatRequest(
        message="找这个云内板子的资料，里面看计量单元",
        context={
            "image_evidences": [
                {
                    "image_evidence_id": "img_text_image_mode",
                    "scene": "document_hint",
                    "summary": "疑似云内 ECU 板卡",
                    "vehicle": {"brand": "云内"},
                    "visible_text": ["MDD01"],
                }
            ]
        },
    )
    active_deps = service._prepare_request_runtime_deps(runtime_deps=deps, request=request, session_id="sess_text_image")

    primary_query, executed_queries, _, body_keyword = asyncio.run(
        service._plan_doc_search_queries(
            request=request,
            active_deps=active_deps,
            fallback_query=request.message,
        )
    )

    assert calls[0]["input_mode"] == "text_image"
    assert "疑似云内 ECU 板卡" in calls[0]["image_evidence"]
    assert primary_query == "云内 MDD01 ECU电路图"
    assert executed_queries[0].confidence == 1.0
    assert body_keyword == "计量单元"


def test_plan_doc_search_queries_uses_image_only_mode(tmp_path, monkeypatch):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())
    calls = []

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        calls.append({"query": query, "image_evidence": image_evidence, "input_mode": input_mode})
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="国方 MDD01 ECU电路图",
            queries=[DocSearchPlannedQuery(query="国方 MDD01 ECU电路图", confidence=0.9)],
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    request = ChatRequest(
        message="",
        context={
            "image_evidences": [
                {
                    "image_evidence_id": "img_only_mode",
                    "scene": "document_hint",
                    "summary": "图片中疑似国方 MDD01 ECU",
                    "visible_text": ["MDD01"],
                }
            ]
        },
    )
    active_deps = service._prepare_request_runtime_deps(runtime_deps=deps, request=request, session_id="sess_image_only")

    primary_query, executed_queries, _, body_keyword = asyncio.run(
        service._plan_doc_search_queries(
            request=request,
            active_deps=active_deps,
            fallback_query="",
        )
    )

    assert calls[0]["input_mode"] == "image"
    assert calls[0]["query"] == ""
    assert "国方 MDD01 ECU" in calls[0]["image_evidence"]
    assert primary_query == "国方 MDD01 ECU电路图"
    assert executed_queries[0].query == "国方 MDD01 ECU电路图"
    assert body_keyword == ""


def test_runtime_processes_image_only_doc_search_with_planner(tmp_path, monkeypatch):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())
    calls = []

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        calls.append({"query": query, "image_evidence": image_evidence, "input_mode": input_mode})
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="国方 MDD01 ECU电路图",
            queries=[DocSearchPlannedQuery(query="国方 MDD01 ECU电路图", confidence=0.9)],
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    response = asyncio.run(
        service.process(
            ChatRequest(
                message="",
                mode="doc_search",
                context={
                    "image_evidences": [
                        {
                            "image_evidence_id": "img_only_workflow",
                            "scene": "document_hint",
                            "summary": "图片中疑似国方 MDD01 ECU",
                            "suggested_queries": ["国方 MDD01 ECU电路图"],
                        }
                    ]
                },
            )
        )
    )

    assert calls[0]["input_mode"] == "image"
    assert calls[0]["query"] == ""
    assert response.type == "documents"
    assert response.content["query"] == "国方 MDD01 ECU电路图"


def test_runtime_uses_planned_body_keyword_for_circuit_body_search(tmp_path, monkeypatch):
    deps = build_deps(tmp_path, FakePreciseSearchEngine)
    enhancer = FakeCircuitBodySearchEnhancer()
    deps.circuit_body_search_enhancer = enhancer

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="东风天锦整车电路图",
            queries=[DocSearchPlannedQuery(query="东风天锦整车电路图", confidence=0.95)],
            body_keyword="油门踏板",
            body_keyword_confidence=0.93,
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="找东风天锦整车电路图里面油门踏板")))

    assert response.type == "documents"
    assert enhancer.calls == [
        {
            "body_keyword": "油门踏板",
            "max_docs": 2,
            "candidate_query": "东风天锦整车电路图",
            "max_candidate_docs": 20,
            "result_count": 2,
        }
    ]
    assert response.content["body_keyword"] == "油门踏板"
    assert response.content["results"][0]["body_search"]["keyword"] == "油门踏板"
    trace_entries = deps.tracer.entries()
    trace_event_types = [entry.event_type for entry in trace_entries]
    assert "circuit_body_search_started" in trace_event_types
    assert "circuit_body_search_completed" in trace_event_types
    assert "circuit_body_search_enhanced" in trace_event_types
    started = next(entry for entry in trace_entries if entry.event_type == "circuit_body_search_started")
    assert started.payload["keyword"] == "油门踏板"
    assert started.payload["candidate_query"] == "东风天锦整车电路图"
    completed = next(entry for entry in trace_entries if entry.event_type == "circuit_body_search_completed")
    assert completed.payload["doc_search_count"] == 2


def test_runtime_exact_title_uses_original_user_query_after_planner_rewrite(tmp_path, monkeypatch):
    deps = build_deps(tmp_path, FakeExactTitleSearchEngine)
    service = AgentLoopService(deps=deps, factory=build_unreachable_factory())

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="青岛解放 中联自卸搅拌车 车身电线束图",
            queries=[DocSearchPlannedQuery(query="青岛解放 中联自卸搅拌车 车身电线束图", confidence=0.95)],
            body_keyword="",
            body_keyword_confidence=0.0,
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    response = asyncio.run(service.process(ChatRequest(message=FakeExactTitleSearchEngine.TITLE)))

    assert response.type == "documents"
    assert response.need_clarify is False
    assert response.content["total"] == 1
    assert response.content["results"][0]["file_id"] == "exact_1"
