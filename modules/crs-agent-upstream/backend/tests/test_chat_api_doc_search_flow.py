from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.factory import AgentFactory
from app.agent.runtime.service import AgentLoopService
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import Settings
from app.legacy.services.clarify_service import ClarifyDecision
from app.main import create_app


class FakeSession:
    def close(self):
        return None


class FakeConfigService:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get(self, key, default=None):
        return self._values.get(key, default)


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


class FakeExternalSearchClient:
    def __init__(self, search_engine_factory):
        self._search_engine_factory = search_engine_factory

    async def search(self, query: str, app_token: str):
        assert app_token == "test-token"
        engine = self._search_engine_factory(FakeSession())
        return engine.search(query, top_k=20, lexical_top_k=200, use_vector=False)


class FakeExternalResultAdapter:
    def adapt_list(self, raw_items, query: str):
        assert raw_items["query"] == query
        return raw_items["results"], raw_items.get("preprocessing") or {}


class FakePlannedSearchEngine:
    queries: list[str] = []

    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        self.__class__.queries.append(query)
        if "电脑板针脚定义" in query:
            return {
                "query": query,
                "results": [
                    {"file_id": "p1", "filename": "云内/电脑板针脚定义/计量单元", "brand": "云内", "series": "德威", "score": 0.71},
                    {"file_id": "dup", "filename": "云内/ECU电路图/计量单元", "brand": "云内", "series": "德威", "score": 0.65},
                ],
                "preprocessing": {"entities": {"brand": ["云内"]}},
                "search_method": "lexical_only",
                "search_time_ms": 11.0,
            }
        return {
            "query": query,
            "results": [
                {"file_id": "dup", "filename": "云内/ECU电路图/计量单元", "brand": "云内", "series": "德威", "score": 0.82},
                {"file_id": "p2", "filename": "云内/发动机电路图/ECU", "brand": "云内", "series": "德威", "score": 0.58},
            ],
            "preprocessing": {"entities": {"brand": ["云内"]}},
            "search_method": "lexical_only",
            "search_time_ms": 9.0,
        }


class FakeDirectQuerySearchEngine:
    queries: list[str] = []

    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        self.__class__.queries.append(query)
        return {
            "query": query,
            "results": [
                {"file_id": "dq1", "filename": f"{query}.txt", "brand": "测试", "series": "直传", "score": 0.66},
            ],
            "preprocessing": {"entities": {}},
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


class FakeFallbackImageCodeSearchEngine:
    queries: list[str] = []

    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        self.__class__.queries.append(query)
        if query == "云内 ECU电路图 计量单元 两线":
            return {
                "query": query,
                "results": [],
                "preprocessing": {"entities": {"brand": ["云内"]}},
                "search_method": "lexical_only",
                "search_time_ms": 8.0,
            }
        if "MDD01" in query:
            return {
                "query": query,
                "results": [
                    {
                        "file_id": "mdd01_hit",
                        "filename": "电路图/ECU电路图/云内/MDD01/国方MDD01发动机电脑板针脚定义.txt",
                        "brand": "云内",
                        "series": "德威",
                        "score": 0.93,
                    }
                ],
                "preprocessing": {"entities": {"brand": ["云内"]}},
                "search_method": "lexical_only",
                "search_time_ms": 7.0,
            }
        return {
            "query": query,
            "results": [],
            "preprocessing": {"entities": {"brand": ["云内"]}},
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


class FakePreprocessingSensitiveSearchEngine:
    queries: list[str] = []

    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        self.__class__.queries.append(query)
        if query == "云内 ECU电路图 计量单元 两线":
            return {
                "query": query,
                "results": [],
                "preprocessing": {
                    "original_query": query,
                    "entities": {"supplier": ["云内"], "doc_type": ["ECU电路图", "针脚定义"]},
                    "query_tokens": ["云内", "ECU", "电路图", "计量单元", "针脚定义"],
                },
                "search_method": "lexical_only",
                "search_time_ms": 8.0,
            }
        if "MDD01" in query:
            return {
                "query": query,
                "results": [
                    {
                        "file_id": "mdd01_hit",
                        "filename": "电路图/ECU电路图/云内/MDD01/国方MDD01发动机电脑板针脚定义.txt",
                        "brand": "云内",
                        "series": "德威",
                        "score": 0.93,
                    }
                ],
                "preprocessing": {
                    "original_query": query,
                    "entities": {"eng_code": ["MDD01"]},
                    "query_tokens": ["MDD01"],
                },
                "search_method": "lexical_only",
                "search_time_ms": 7.0,
            }
        return {
            "query": query,
            "results": [],
            "preprocessing": {"original_query": query, "entities": {}, "query_tokens": []},
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


class FakeSensitiveExistenceValidator:
    def validate(self, results, preprocessing):
        from types import SimpleNamespace

        original_query = str((preprocessing or {}).get("original_query") or "")
        if results and "MDD01" not in original_query:
            return SimpleNamespace(
                status="no_match",
                query_entities=(preprocessing or {}).get("entities", {}),
                matched_entities={},
                unmatched_entities={"keyword": ["MDD01"]},
                suggestions={},
                message="资料库中暂无「MDD01」的相关资料",
                should_continue=False,
            )
        return SimpleNamespace(
            status="exact_match",
            query_entities=(preprocessing or {}).get("entities", {}),
            matched_entities={},
            unmatched_entities={},
            suggestions={},
            message=None,
            should_continue=True,
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
        config_service=FakeConfigService({"agent_model": "test", "openrouter_clarify_model": "test"}),
        dimension_service=dimension_service,
        existence_validator=existence_validator,
        hard_constraint_validator=hard_constraint_validator,
        ggzj_search_client=FakeExternalSearchClient(search_engine_factory),
        ggzj_result_adapter=FakeExternalResultAdapter(),
    )


def build_unreachable_factory() -> AgentFactory:
    def llm(_messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
        raise AssertionError("doc_search workflow should not call the LLM")

    return AgentFactory(
        settings=Settings(agent_model="test"),
        model_override=FunctionModel(llm),
    )


def _install_test_runtime(app, deps: AgentRuntimeDeps, factory: AgentFactory) -> None:
    app.state.runtime_deps = deps
    app.state.db_session_factory = deps.db_session_factory
    app.state.agent_service = AgentLoopService(deps=deps, factory=factory)


def test_chat_api_doc_search_invalidity_roundtrip(tmp_path):
    deps = build_doc_search_deps(
        tmp_path,
        FakeInvalidSearchEngine,
        hard_constraint_validator=FakeHardConstraintValidator(),
    )
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)
        response = client.post(
            "/chat/completions",
            json={"message": "D999 电路图"},
            headers={"x-app-token": "test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "message"
    assert body["content"]["message"] == "抱歉，暂无相关资料在数据库中。"
    assert body["content"]["should_archive_previous"] is True
    assert body["business"] == "DOC_SEARCH"


def test_chat_api_doc_search_ask_user_selection_payload_roundtrip(tmp_path):
    deps = build_doc_search_deps(tmp_path, FakeAmbiguousSearchEngine)
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)

        first = client.post(
            "/chat/completions",
            json={"message": "帮我找东风电路图"},
            headers={"x-app-token": "test-token"},
        )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["type"] == "ask_user"
        assert first_body["ask_user"]["question"] == "请选择车型系列："
        assert first_body["clarify_options"][0]["selection_payload"] == {
            "filters": {"brand": "东风", "series": "天锦"},
            "file_ids": [],
        }
        assert first_body["ask_user"]["context"]["message"] == "找到 6 个相关结果。请选择车型系列："
        assert first_body["ask_user"]["context"]["query"] == "帮我找东风电路图"

        second = client.post(
            "/chat/completions",
            json={
                "session_id": first_body["session_id"],
                "ask_user_answer": {
                    "tool_call_id": first_body["ask_user"]["tool_call_id"],
                    "answer": "天锦",
                    "metadata": {
                        "selection_payload": first_body["clarify_options"][0]["selection_payload"],
                    },
                },
            },
            headers={"x-app-token": "test-token"},
        )

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["type"] == "documents"
    assert second_body["business"] == "DOC_SEARCH"
    assert second_body["content"]["total"] == 3
    assert all("天锦" in item["series"] for item in second_body["content"]["results"])


def test_chat_api_doc_search_other_selection_roundtrip(tmp_path):
    deps = build_doc_search_deps(tmp_path, FakeAmbiguousSearchEngine, clarify_service=FakeClarifyServiceWithOther())
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)

        first = client.post(
            "/chat/completions",
            json={"message": "帮我找东风电路图"},
            headers={"x-app-token": "test-token"},
        )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["type"] == "ask_user"

        other_option = next(option for option in first_body["clarify_options"] if option["label"] == "其他")
        assert other_option["selection_payload"] == {
            "filters": {"brand": "东风"},
            "file_ids": ["3", "4", "5", "6"],
        }

        second = client.post(
            "/chat/completions",
            json={
                "session_id": first_body["session_id"],
                "ask_user_answer": {
                    "tool_call_id": first_body["ask_user"]["tool_call_id"],
                    "answer": "其他",
                    "metadata": {
                        "selection_payload": {},
                    },
                },
            },
            headers={"x-app-token": "test-token"},
        )

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["type"] == "documents"
    assert second_body["business"] == "DOC_SEARCH"
    assert second_body["content"]["total"] == 4
    assert {item["series"] for item in second_body["content"]["results"]} == {"天锦KR", "天龙KL", "天锦VR", "天龙旗舰"}


def test_chat_api_doc_search_uses_planned_queries_and_deduplicates_results(tmp_path, monkeypatch):
    from app.agent.domain.doc_search.models import DocSearchPlannedQuery, DocSearchQueryPlan
    from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner

    FakePlannedSearchEngine.queries = []

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        assert "云内" in query
        assert "图片证据" not in query
        assert image_evidence or known_slots
        assert input_mode == "text_image"
        return DocSearchQueryPlan(
            input_mode=input_mode,
            primary_query="云内 ECU电路图 计量单元 两线",
            queries=[
                DocSearchPlannedQuery(query="云内 ECU电路图 计量单元 两线", confidence=0.92),
                DocSearchPlannedQuery(query="云内 电脑板针脚定义 计量单元", confidence=0.86),
            ],
            rationale="优先使用品牌+ECU+资料类型组合。",
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    deps = build_doc_search_deps(tmp_path, FakePlannedSearchEngine, clarify_service=FakeClarifyService())
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)
        response = client.post(
            "/chat/completions",
            json={
                "message": "这个板子是哪个，带计量单元2线的云内",
                "mode": "auto",
                "context": {
                    "image_evidences": [
                        {
                            "image_evidence_id": "img_1",
                            "scene": "document_hint",
                            "summary": "疑似云内 ECU 板卡",
                            "vehicle": {"brand": "云内", "series": "德威"},
                            "visible_text": ["国方电子", "JB1037", "MDD01 / ECUA-00-000056"],
                            "suggested_queries": ["云内 ECU电路图", "云内 电脑板针脚定义"],
                        }
                    ]
                },
            },
            headers={"x-app-token": "test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "documents"
    assert body["business"] == "DOC_SEARCH"
    assert FakePlannedSearchEngine.queries[:2] == [
        "云内 ECU电路图 计量单元 两线",
        "云内 电脑板针脚定义 计量单元",
    ]
    assert "国方MDD01" in FakePlannedSearchEngine.queries or "国方 MDD01" in FakePlannedSearchEngine.queries
    assert [item["file_id"] for item in body["content"]["results"]] == ["dup", "p1", "p2"]
    planned_query_texts = [item["query"] for item in body["content"]["planned_queries"]]
    assert planned_query_texts[:2] == [
        "云内 ECU电路图 计量单元 两线",
        "云内 电脑板针脚定义 计量单元",
    ]
    assert any(query in planned_query_texts for query in ("国方MDD01", "国方 MDD01", "云内 MDD01"))


def test_chat_api_doc_search_appends_image_code_hint_queries(tmp_path, monkeypatch):
    from app.agent.domain.doc_search.models import DocSearchPlannedQuery, DocSearchQueryPlan
    from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner

    FakePlannedSearchEngine.queries = []

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
            primary_query="云内 ECU电路图 计量单元 两线",
            queries=[
                DocSearchPlannedQuery(query="云内 ECU电路图 计量单元 两线", confidence=0.92),
            ],
            rationale="主查询优先使用品牌和资料类型。",
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    deps = build_doc_search_deps(tmp_path, FakePlannedSearchEngine, clarify_service=FakeClarifyService())
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)
        response = client.post(
            "/chat/completions",
            json={
                "message": "这个板子资料是哪个，带计量单元2线的云内发动机",
                "mode": "auto",
                "context": {
                    "image_evidences": [
                        {
                            "image_evidence_id": "img_ecu_1",
                            "scene": "document_hint",
                            "summary": "疑似云内 ECU 板卡",
                            "vehicle": {"brand": "云内"},
                            "visible_text": [
                                "国方电子",
                                "JB1037",
                                "MDD01 / ECUA-00-000056",
                                "苏州国方汽车电子有限公司",
                            ],
                            "suggested_queries": ["云内 ECU电路图"],
                        }
                    ]
                },
            },
            headers={"x-app-token": "test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "documents"
    assert body["business"] == "DOC_SEARCH"
    assert any(query in FakePlannedSearchEngine.queries for query in ("国方MDD01", "国方 MDD01", "云内 MDD01"))


def test_chat_api_doc_search_uses_later_image_code_hits_when_primary_query_misses(tmp_path, monkeypatch):
    from app.agent.domain.doc_search.models import DocSearchPlannedQuery, DocSearchQueryPlan
    from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner

    FakeFallbackImageCodeSearchEngine.queries = []

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
            primary_query="云内 ECU电路图 计量单元 两线",
            queries=[DocSearchPlannedQuery(query="云内 ECU电路图 计量单元 两线", confidence=0.92)],
            rationale="先试主资料类型搜索。",
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    deps = build_doc_search_deps(tmp_path, FakeFallbackImageCodeSearchEngine, clarify_service=FakeClarifyService())
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)
        response = client.post(
            "/chat/completions",
            json={
                "message": "这个板子资料是哪个，带计量单元2线的云内发动机",
                "mode": "auto",
                "context": {
                    "image_evidences": [
                        {
                            "image_evidence_id": "img_ecu_fallback",
                            "scene": "document_hint",
                            "summary": "疑似云内 ECU 板卡",
                            "vehicle": {"brand": "云内"},
                            "visible_text": ["国方电子", "JB1037", "MDD01 / ECUA-00-000056"],
                            "suggested_queries": ["云内 ECU电路图"],
                        }
                    ]
                },
            },
            headers={"x-app-token": "test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "documents"
    assert body["business"] == "DOC_SEARCH"
    assert any(
        query in FakeFallbackImageCodeSearchEngine.queries
        for query in ("国方MDD01", "国方 MDD01", "云内 MDD01", "MDD01")
    )
    assert [item["file_id"] for item in body["content"]["results"]] == ["mdd01_hit"]


def test_chat_api_doc_search_revalidates_merged_results_with_precise_image_query_preprocessing(tmp_path, monkeypatch):
    from app.agent.domain.doc_search.models import DocSearchPlannedQuery, DocSearchQueryPlan
    from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner

    FakePreprocessingSensitiveSearchEngine.queries = []

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
            primary_query="云内 ECU电路图 计量单元 两线",
            queries=[DocSearchPlannedQuery(query="云内 ECU电路图 计量单元 两线", confidence=0.92)],
            rationale="先试资料类型，再回退到图片识别到的型号词。",
        )

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    deps = build_doc_search_deps(
        tmp_path,
        FakePreprocessingSensitiveSearchEngine,
        clarify_service=FakeClarifyService(),
        existence_validator=FakeSensitiveExistenceValidator(),
    )
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)
        response = client.post(
            "/chat/completions",
            json={
                "message": "这个板子资料是哪个，带计量单元2线的云内发动机",
                "mode": "auto",
                "context": {
                    "image_evidences": [
                        {
                            "image_evidence_id": "img_ecu_precise_preprocessing",
                            "scene": "document_hint",
                            "summary": "疑似云内 ECU 板卡",
                            "vehicle": {"brand": "云内"},
                            "visible_text": ["国方电子", "JB1037", "MDD01 / ECUA-00-000056"],
                            "suggested_queries": ["云内 ECU电路图"],
                        }
                    ]
                },
            },
            headers={"x-app-token": "test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "documents"
    assert body["business"] == "DOC_SEARCH"
    assert any(
        query in FakePreprocessingSensitiveSearchEngine.queries
        for query in ("国方MDD01", "国方 MDD01", "云内 MDD01", "MDD01")
    )
    assert [item["file_id"] for item in body["content"]["results"]] == ["mdd01_hit"]


def test_chat_api_doc_search_without_images_uses_text_mode_planner_fallback(tmp_path, monkeypatch):
    from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner

    FakeDirectQuerySearchEngine.queries = []
    planner_calls = []

    async def fake_plan(
        self,
        *,
        query: str,
        image_evidence: str = "",
        known_slots: str = "",
        input_mode: str = "text",
    ):
        planner_calls.append(
            {
                "query": query,
                "image_evidence": image_evidence,
                "known_slots": known_slots,
                "input_mode": input_mode,
            }
        )
        return None

    monkeypatch.setattr(PydanticAIDocSearchQueryPlanner, "plan", fake_plan)

    deps = build_doc_search_deps(tmp_path, FakeDirectQuerySearchEngine, clarify_service=FakeClarifyService())
    factory = build_unreachable_factory()
    app = create_app()

    with TestClient(app) as client:
        _install_test_runtime(app, deps, factory)
        response = client.post(
            "/chat/completions",
            json={
                "message": "帮我找云内带计量单元2线的板子资料",
                "mode": "auto",
            },
            headers={"x-app-token": "test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "documents"
    assert body["business"] == "DOC_SEARCH"
    assert planner_calls == [
        {
            "query": "帮我找云内带计量单元2线的板子资料",
            "image_evidence": "",
            "known_slots": "",
            "input_mode": "text",
        }
    ]
    assert FakeDirectQuerySearchEngine.queries == ["帮我找云内带计量单元2线的板子资料"]
    assert body["content"]["query"] == "帮我找云内带计量单元2线的板子资料"
    assert body["content"].get("planned_queries") in (None, [])
