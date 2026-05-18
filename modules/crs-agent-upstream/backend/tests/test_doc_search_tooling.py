import asyncio

from app.agent.adapters.legacy_doc_search_adapter import LegacyDocSearchAdapter
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings
from pydantic_ai.models.function import FunctionModel
from app.legacy.services.clarify_service import ClarifyDecision
from app.schemas.chat import ChatRequest


class FakeSession:
    def close(self):
        return None


class FakeSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {"file_id": "1", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.35},
                {"file_id": "2", "brand": "解放", "series": "J6", "doc_types": ["电路图"], "score": 0.22},
            ],
            "preprocessing": {"entities": {"brand": ["东风"]}},
            "search_method": "lexical_only",
            "search_time_ms": 12.5,
        }


class FakeAmbiguousSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {"file_id": "1", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.45},
                {"file_id": "2", "brand": "东风", "series": "天龙", "doc_types": ["电路图"], "score": 0.41},
                {"file_id": "3", "brand": "东风", "series": "天锦KR", "doc_types": ["电路图"], "score": 0.39},
                {"file_id": "4", "brand": "东风", "series": "天龙KL", "doc_types": ["电路图"], "score": 0.37},
                {"file_id": "5", "brand": "东风", "series": "天锦VR", "doc_types": ["电路图"], "score": 0.35},
                {"file_id": "6", "brand": "东风", "series": "天龙旗舰", "doc_types": ["电路图"], "score": 0.33},
            ],
            "preprocessing": {"entities": {"brand": ["东风"]}},
            "search_method": "lexical_only",
            "search_time_ms": 18.0,
        }


class FakeInvalidSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {"file_id": "1", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.10},
            ],
            "preprocessing": {"entities": {"platform": ["D999"]}, "query_tokens": ["D999"]},
            "search_method": "lexical_only",
            "search_time_ms": 10.0,
        }


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

    def apply_choice(self, results, facet, choice):
        filtered = []
        for item in results:
            value = item.get(facet)
            if isinstance(value, list):
                if choice in value:
                    filtered.append(item)
            elif value == choice:
                filtered.append(item)
        return filtered or results

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


class FakeHardConstraintResult:
    def __init__(self, ok: bool, missing_tokens=None, checked_tokens=None, message=None):
        self.ok = ok
        self.missing_tokens = missing_tokens or []
        self.checked_tokens = checked_tokens or []
        self.message = message


class FakeHardConstraintValidator:
    def validate(self, results, preprocessing):
        return FakeHardConstraintResult(
            ok=False,
            missing_tokens=["D999"],
            checked_tokens=["D999"],
            message="抱歉，暂无相关资料在数据库中。",
        )


def build_doc_search_deps(
    tmp_path,
    search_engine_factory,
    *,
    clarify_service=None,
    dimension_service=None,
    existence_validator=None,
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
        dimension_service=dimension_service,
        existence_validator=existence_validator,
        hard_constraint_validator=hard_constraint_validator,
    )


def build_unreachable_factory() -> AgentFactory:
    def llm(*_args, **_kwargs):
        raise AssertionError("doc_search workflow should not call the LLM")

    return AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )


def test_legacy_doc_search_adapter_applies_structured_filters(tmp_path):
    deps = build_doc_search_deps(tmp_path, FakeSearchEngine)
    adapter = LegacyDocSearchAdapter(deps)

    result = asyncio.run(adapter.search("东风电路图", filters={"brand": "东风"}, top_k=10))

    assert result["status"] == "ok"
    assert result["data"]["total"] == 1
    assert result["data"]["results"][0]["brand"] == "东风"
    assert result["data"]["applied_filters"] == {"brand": "东风"}


def test_legacy_doc_search_adapter_offloads_search_to_thread(tmp_path, monkeypatch):
    deps = build_doc_search_deps(tmp_path, FakeSearchEngine)
    adapter = LegacyDocSearchAdapter(deps)
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(adapter.search("东风电路图", filters={"brand": "东风"}, top_k=10))

    assert result["status"] == "ok"
    assert len(calls) == 1
    assert calls[0][0].__self__ is adapter._service


def test_legacy_doc_search_adapter_returns_invalidity_when_hard_constraint_fails(tmp_path):
    deps = build_doc_search_deps(
        tmp_path,
        FakeInvalidSearchEngine,
        hard_constraint_validator=FakeHardConstraintValidator(),
    )
    adapter = LegacyDocSearchAdapter(deps)

    result = asyncio.run(adapter.search("D999 电路图", top_k=10))

    assert result["status"] == "ok"
    assert result["data"]["results"] == []
    assert result["data"]["total"] == 0
    assert result["data"]["validity"]["has_valid_results"] is False
    assert result["data"]["validity"]["reason"] == "hard_constraint_no_match"
    assert result["data"]["validity"]["hard_constraint"]["missing_tokens"] == ["D999"]


def test_tool_registry_excludes_doc_search_agent_tools():
    registry = build_default_tool_registry()

    assert registry.get("search_documents") is None
    assert registry.get("analyze_doc_search_ambiguity") is None


def test_agent_loop_uses_deterministic_doc_search_ask_user(tmp_path):
    deps = build_doc_search_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    service = AgentLoopService(deps=deps, factory=factory)

    response = asyncio.run(service.process(ChatRequest(message="帮我找东风电路图")))

    assert response.type == "ask_user"
    assert response.need_clarify is True
    assert response.ask_user is not None
    assert response.ask_user.question == "请选择车型系列："
    assert [option.key for option in response.ask_user.options] == ["天锦", "天龙"]
    assert response.ask_user.options[0].selection_payload.filters == {"brand": "东风", "series": "天锦"}
    assert response.ask_user.context["message"] == "找到 6 个相关结果。请选择车型系列："
    assert response.clarify_options[0].selection_payload == {
        "filters": {"brand": "东风", "series": "天锦"},
        "file_ids": [],
    }


def test_legacy_doc_search_adapter_selection_payload_filters_results(tmp_path):
    deps = build_doc_search_deps(tmp_path, FakeSearchEngine)
    adapter = LegacyDocSearchAdapter(deps)

    result = asyncio.run(
        adapter.search(
            "电路图",
            selection_payload={"filters": {"brand": "东风"}},
            top_k=10,
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["applied_filters"] == {"brand": "东风"}
    assert result["data"]["total"] == 1
    assert result["data"]["results"][0]["brand"] == "东风"
