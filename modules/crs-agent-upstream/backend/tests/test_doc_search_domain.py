import asyncio
import types

from app.agent.domain.doc_search.llm_smart import PydanticAIDocSearchLLMClarifyService
from app.agent.domain.doc_search.query_planner import PydanticAIDocSearchQueryPlanner
from app.agent.domain.doc_search.models import (
    DocSearchLLMClarifyOption,
    DocSearchLLMClarifyResult,
    DocSearchPlannedQuery,
    DocSearchQueryPlan,
    DocSearchRequest,
)
from app.agent.domain.doc_search.service import DocSearchService
from app.legacy.services.clarify_service import ClarifyDecision


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


class RepeatedSeriesAmbiguousSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        assert use_vector is False
        return {
            "query": query,
            "results": [
                {"file_id": "1", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.45},
                {"file_id": "2", "brand": "东风", "series": "天龙", "doc_types": ["电路图"], "score": 0.41},
                {"file_id": "3", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.39},
                {"file_id": "4", "brand": "东风", "series": "天龙", "doc_types": ["电路图"], "score": 0.37},
                {"file_id": "5", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.35},
                {"file_id": "6", "brand": "东风", "series": "天龙", "doc_types": ["电路图"], "score": 0.33},
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


class PublicClarifyServiceOnly:
    @property
    def facet_field_map(self):
        return {
            "brand": "brand",
            "series": "series",
            "model": "model",
            "doc_type": "doc_types",
            "emissions": "emissions",
        }

    def analyze(self, results, preprocessing=None, existing_filters=None, clarify_round=0):
        return ClarifyDecision(need=False, facet=None, question=None, options=[], reason=None)


class FakeExistenceResult:
    def __init__(self, status: str = "exact_match", message: str | None = None):
        self.status = status
        self.query_entities = {}
        self.matched_entities = {}
        self.unmatched_entities = {}
        self.suggestions = []
        self.message = message
        self.should_continue = True


class FakeExistenceValidator:
    def __init__(self):
        self.last_results = None

    def validate(self, results, preprocessing):
        self.last_results = list(results)
        return FakeExistenceResult()


class FakeHardConstraintResult:
    def __init__(self, ok: bool, missing_tokens=None, checked_tokens=None, message=None):
        self.ok = ok
        self.missing_tokens = missing_tokens or []
        self.checked_tokens = checked_tokens or []
        self.message = message


class FakeHardConstraintValidator:
    def __init__(self, *, ok: bool = True, message: str | None = None):
        self._ok = ok
        self._message = message

    def validate(self, results, preprocessing):
        return FakeHardConstraintResult(
            ok=self._ok,
            missing_tokens=[] if self._ok else ["D999"],
            checked_tokens=["D999"] if not self._ok else [],
            message=self._message,
        )


def build_service(
    search_engine_factory,
    *,
    clarify_service=None,
    dimension_service=None,
    existence_validator=None,
    hard_constraint_validator=None,
    llm_clarify_service=None,
    config_service=None,
) -> DocSearchService:
    return DocSearchService(
        db_session_factory=lambda: FakeSession(),
        search_engine_factory=search_engine_factory,
        clarify_service=clarify_service or FakeClarifyService(),
        dimension_service=dimension_service,
        existence_validator=existence_validator or FakeExistenceValidator(),
        hard_constraint_validator=hard_constraint_validator or FakeHardConstraintValidator(),
        search_top_k_lex=200,
        llm_clarify_service=llm_clarify_service,
        config_service=config_service,
    )


def test_doc_search_service_executes_fixed_pipeline():
    service = build_service(FakeSearchEngine)

    result = service.execute(
        DocSearchRequest(
            query="东风电路图",
            filters={"brand": "东风"},
            top_k=10,
        )
    )

    assert result.total == 1
    assert result.results[0]["brand"] == "东风"
    assert result.applied_filters == {"brand": "东风"}
    assert result.validity.has_valid_results is True


def test_doc_search_service_does_not_require_clarify_private_matchers():
    service = build_service(FakeSearchEngine, clarify_service=PublicClarifyServiceOnly())

    result = service.execute(
        DocSearchRequest(
            query="东风电路图",
            filters={"brand": "东风"},
            top_k=10,
        )
    )

    assert result.total == 1
    assert result.results[0]["brand"] == "东风"
    assert result.applied_filters == {"brand": "东风"}


def test_doc_search_service_returns_invalidity_from_validators():
    service = build_service(
        FakeInvalidSearchEngine,
        hard_constraint_validator=FakeHardConstraintValidator(
            ok=False,
            message="抱歉，暂无相关资料在数据库中。",
        ),
    )

    result = service.execute(DocSearchRequest(query="D999 电路图", top_k=10))

    assert result.results == []
    assert result.total == 0
    assert result.validity.has_valid_results is False
    assert result.validity.reason == "hard_constraint_no_match"
    assert result.validity.hard_constraint is not None
    assert result.validity.hard_constraint.missing_tokens == ["D999"]


def test_doc_search_service_skips_hard_constraint_when_disabled():
    service = build_service(
        FakeInvalidSearchEngine,
        hard_constraint_validator=FakeHardConstraintValidator(
            ok=False,
            message="抱歉，暂无相关资料在数据库中。",
        ),
        config_service=FakeConfigService({"hard_constraint_enabled": False}),
    )

    result = service.execute(DocSearchRequest(query="D999 电路图", top_k=10))

    assert result.total == 1
    assert result.results[0]["file_id"] == "1"
    assert result.validity.has_valid_results is True
    assert result.validity.hard_constraint is None


def test_doc_search_service_analyzes_ambiguity():
    service = build_service(FakeAmbiguousSearchEngine)
    search_result = service.execute(DocSearchRequest(query="东风电路图", top_k=20))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing=search_result.preprocessing,
            existing_filters=search_result.applied_filters,
            query=search_result.original_query,
            validity=search_result.validity.model_dump(mode="json"),
        )
    )

    assert analysis.need_clarify is True
    assert analysis.facet == "series"
    assert analysis.question == "请选择车型系列："
    assert analysis.options[0].selection_payload.filters == {"brand": "东风", "series": "天锦"}
    assert analysis.context is not None
    assert analysis.context.message == "找到 6 个相关结果。请选择车型系列："
    assert analysis.context.query == "东风电路图"


class AutoFilterSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        results = []
        for idx in range(60):
            results.append(
                {
                    "file_id": f"hy_{idx}",
                    "filename": f"东风红岩测试电路图_{idx}",
                    "brand": "东风",
                    "series": "红岩",
                    "doc_types": ["电路图"],
                    "score": 0.95 - idx * 0.001,
                }
            )
        for idx in range(50):
            results.append(
                {
                    "file_id": f"tl_{idx}",
                    "filename": f"东风天龙测试电路图_{idx}",
                    "brand": "东风",
                    "series": "天龙",
                    "doc_types": ["电路图"],
                    "score": 0.85 - idx * 0.001,
                }
            )
        for idx in range(6):
            results.append(
                {
                    "file_id": f"tj_{idx}",
                    "filename": f"东风天锦测试电路图_{idx}",
                    "brand": "东风",
                    "series": "天锦",
                    "doc_types": ["电路图"],
                    "score": 0.65 - idx * 0.001,
                }
            )
        return {
            "query": query,
            "results": results,
            "preprocessing": {
                "entities": {
                    "brand": ["东风"],
                    "series": ["天锦"],
                    "doc_type": ["电路图"],
                    "platform": [],
                }
            },
            "search_method": "lexical_only",
            "search_time_ms": 8.0,
        }


class ParentBackfillSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        return {
            "query": query,
            "results": [
                {"file_id": "1", "brand": "东风", "series": "天锦", "doc_types": ["电路图"], "score": 0.5},
                {"file_id": "2", "brand": "解放", "series": "J6", "doc_types": ["电路图"], "score": 0.3},
            ],
            "preprocessing": {"entities": {"series": ["天锦"]}},
            "search_method": "lexical_only",
            "search_time_ms": 5.0,
        }


class EngCodeSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        return {
            "query": query,
            "results": [
                {"file_id": "1", "eng_codes": ["D530"], "filename": "D530 电路图", "score": 0.4},
                {"file_id": "2", "eng_codes": ["D310"], "filename": "D310 电路图", "score": 0.3},
            ],
            "preprocessing": {"entities": {}},
            "search_method": "lexical_only",
            "search_time_ms": 4.0,
        }


class DocTypeFallbackSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        return {
            "query": query,
            "results": [
                {
                    "file_id": "target",
                    "filename": "东风新天龙KL-D320_整车线束图",
                    "hierarchy_full": "整车电路图->东风->天龙",
                    "doc_types": None,
                    "score": 0.4,
                },
                {
                    "file_id": "other",
                    "filename": "东风天龙_维修手册",
                    "hierarchy_full": "维保资料->东风->天龙",
                    "doc_types": ["维修手册"],
                    "score": 0.3,
                },
            ],
            "preprocessing": {"entities": {}},
            "search_method": "lexical_only",
            "search_time_ms": 4.0,
        }


class FakeDimensionService:
    def __init__(self, *, parent_map=None, root_map=None, doc_type_patterns=None, conflicts=None):
        self.is_loaded = True
        self._parent_map = parent_map or {}
        self._root_map = root_map or {}
        self._doc_type_patterns = doc_type_patterns or {}
        self._conflicts = conflicts or []
        self._values = {
            "doc_type": {
                key: type("Cfg", (), {"patterns": value})()
                for key, value in self._doc_type_patterns.items()
            }
        }

    def get_root_value_in_facet(self, facet_key: str, value: str) -> str:
        return self._root_map.get((facet_key, value), value)

    def get_parent(self, facet_key: str, value: str):
        return self._parent_map.get((facet_key, value))

    def find_value_by_pattern(self, raw: str):
        if raw not in self._doc_type_patterns:
            return None
        return ("doc_type", raw, self._doc_type_patterns[raw])

    def detect_conflicts(self, entities):
        return list(self._conflicts)


def test_doc_search_service_auto_filters_before_existence_validation():
    existence_validator = FakeExistenceValidator()
    service = build_service(
        AutoFilterSearchEngine,
        existence_validator=existence_validator,
    )

    result = service.execute(DocSearchRequest(query="东风天锦电路图", top_k=20))

    assert result.total == 6
    assert result.applied_filters["brand"] == "东风"
    assert result.applied_filters["series"] == "天锦"
    assert result.applied_filters["doc_type"] == "电路图"
    assert existence_validator.last_results is not None
    assert len(existence_validator.last_results) == 6
    assert all(item["series"] == "天锦" for item in existence_validator.last_results)


def test_doc_search_service_parent_backfills_brand_from_series():
    dimension_service = FakeDimensionService(
        parent_map={("series", "天锦"): ("brand", "东风")},
    )
    service = build_service(
        ParentBackfillSearchEngine,
        dimension_service=dimension_service,
    )

    result = service.execute(DocSearchRequest(query="天锦电路图", top_k=10))

    assert result.total == 1
    assert result.applied_filters["series"] == "天锦"
    assert result.applied_filters["brand"] == "东风"
    assert result.results[0]["brand"] == "东风"


def test_doc_search_service_structured_eng_code_filter_is_not_applied():
    service = build_service(EngCodeSearchEngine)

    result = service.execute(
        DocSearchRequest(
            query="530 电路图",
            filters={"eng_code": "530"},
            top_k=10,
        )
    )

    assert result.total == 2
    assert "eng_code" not in result.applied_filters
    assert [item["file_id"] for item in result.results] == ["1", "2"]


def test_doc_search_service_structured_doc_type_filter_falls_back_to_filename():
    dimension_service = FakeDimensionService(
        doc_type_patterns={"整车电路图": ["整车图", "整车电路图", "整车线束图"]},
    )
    service = build_service(
        DocTypeFallbackSearchEngine,
        dimension_service=dimension_service,
    )

    result = service.execute(
        DocSearchRequest(
            query="整车图",
            filters={"doc_type": "整车电路图"},
            top_k=10,
        )
    )

    assert result.total == 1
    assert result.applied_filters["doc_type"] == "整车电路图"
    assert result.results[0]["file_id"] == "target"


class SpecificDocTypeMixedSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        del query, top_k, lexical_top_k, use_vector
        return {
            "query": "东风天龙D310_整车电路图",
            "results": [
                {
                    "file_id": "whole-1",
                    "filename": "东风天龙D310_国四_整车电路图",
                    "brand": "东风",
                    "series": "天龙",
                    "doc_types": ["整车电路图"],
                    "score": 0.93,
                },
                {
                    "file_id": "whole-2",
                    "filename": "东风天龙D310_国五_整车电路图",
                    "brand": "东风",
                    "series": "天龙",
                    "doc_types": ["整车电路图"],
                    "score": 0.9,
                },
                {
                    "file_id": "part-1",
                    "filename": "东风天龙D310_仪表系统电路图",
                    "brand": "东风",
                    "series": "天龙",
                    "doc_types": ["电路图"],
                    "score": 0.88,
                },
            ],
            "preprocessing": {
                "entities": {
                    "brand": ["东风"],
                    "series": ["天龙"],
                    "doc_type": ["电路图", "整车电路图"],
                }
            },
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


def test_doc_search_service_prefers_specific_doc_type_over_generic_doc_type():
    service = build_service(
        SpecificDocTypeMixedSearchEngine,
        dimension_service=FakeDimensionService(),
    )

    result = service.execute(DocSearchRequest(query="东风天龙D310_整车电路图", top_k=20))

    assert result.applied_filters["doc_type"] == "整车电路图"
    assert [item["file_id"] for item in result.results] == ["whole-1", "whole-2"]


class NoTruncateSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        results = []
        for idx in range(25):
            results.append(
                {
                    "file_id": str(idx + 1),
                    "filename": f"东风电路图_{idx + 1}",
                    "brand": "东风",
                    "series": "天锦",
                    "doc_types": ["电路图"],
                    "score": 0.9 - idx * 0.01,
                }
            )
        return {
            "query": query,
            "results": results,
            "preprocessing": {"entities": {"brand": ["东风"]}},
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


class MixedDocTypeSearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        return {
            "query": query,
            "results": [
                {
                    "file_id": "1",
                    "filename": "三一挖掘机整车电路图",
                    "brand": "三一",
                    "series": "SY215",
                    "doc_types": ["电路图"],
                    "score": 0.91,
                },
                {
                    "file_id": "2",
                    "filename": "三一挖掘机 ECU 针脚定义",
                    "brand": "三一",
                    "series": "SY215",
                    "doc_types": ["针脚定义"],
                    "score": 0.89,
                },
                {
                    "file_id": "3",
                    "filename": "三一挖掘机液压电路图",
                    "brand": "三一",
                    "series": "SY215",
                    "doc_types": ["电路图"],
                    "score": 0.88,
                },
            ],
            "preprocessing": {"entities": {"brand": ["三一"], "doc_type": ["电路图"]}},
            "search_method": "lexical_only",
            "search_time_ms": 5.0,
        }


def test_doc_search_service_does_not_truncate_post_processed_results():
    service = build_service(NoTruncateSearchEngine)

    result = service.execute(DocSearchRequest(query="东风电路图", top_k=10))

    assert result.total == 25
    assert len(result.results) == 25
    assert result.summary == "找到 25 个「东风 电路图」相关文档"


def test_doc_search_service_auto_filters_doc_type_from_query_entities():
    service = build_service(MixedDocTypeSearchEngine)

    result = service.execute(DocSearchRequest(query="三一挖掘机电路图", top_k=10))

    assert result.applied_filters["brand"] == "三一"
    assert result.applied_filters["doc_type"] == "电路图"
    assert [item["file_id"] for item in result.results] == ["1", "3"]
    assert all("针脚" not in (item.get("filename") or "") for item in result.results)


class FakeConflict:
    type = "parent_mismatch"
    message = "「J6P」是解放的系列，与您输入的「东风」不一致"
    options = [
        {
            "key": "{\"brand\":\"解放\",\"series\":\"J6P\"}",
            "label": "解放 J6P",
            "description": "J6P 是解放的系列",
            "filters": {"brand": "解放", "series": "J6P"},
        },
        {
            "key": "{\"brand\":\"东风\"}",
            "label": "东风",
            "description": "只按品牌筛选",
            "filters": {"brand": "东风"},
        },
    ]


def test_doc_search_service_detects_conflict_before_normal_clarify():
    dimension_service = FakeDimensionService(conflicts=[FakeConflict()])
    service = build_service(
        FakeAmbiguousSearchEngine,
        dimension_service=dimension_service,
    )
    search_result = service.execute(DocSearchRequest(query="东风 J6P 电路图", top_k=20))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing={"entities": {"brand": ["东风"], "series": ["J6P"]}},
            existing_filters={},
            query="东风 J6P 电路图",
        )
    )

    assert analysis.need_clarify is True
    assert analysis.facet == "_conflict"
    assert analysis.question == "「J6P」是解放的系列，与您输入的「东风」不一致"
    assert analysis.options[0].selection_payload.filters == {"brand": "解放", "series": "J6P"}


def test_doc_search_service_detects_multi_brand_ambiguity_before_normal_clarify():
    dimension_service = FakeDimensionService(conflicts=[])
    service = build_service(
        FakeAmbiguousSearchEngine,
        dimension_service=dimension_service,
    )
    search_result = service.execute(DocSearchRequest(query="东风 三一 电路图", top_k=20))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing={"entities": {"brand": ["东风", "三一"]}},
            existing_filters={},
            query="东风 三一 电路图",
        )
    )

    assert analysis.need_clarify is True
    assert analysis.facet == "brand"
    assert analysis.question == "检测到多个品牌，请先选择您要查找的品牌："
    assert [option.label for option in analysis.options] == ["东风", "三一"]
    assert analysis.options[1].selection_payload.filters == {"brand": "三一"}


class FakeNoRuleClarifyService(FakeClarifyService):
    def analyze(self, results, preprocessing=None, existing_filters=None, clarify_round=0):
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


class FakeLLMClarifyService:
    async def analyze(self, *, results, query, existing_filters, user_intent_entities=None):
        assert query == "东风电路图"
        assert existing_filters == {"brand": "东风"}
        return DocSearchLLMClarifyResult(
            question="请选择资料版本：",
            dimension="variant",
            reason="llm_smart_clarify",
            options=[
                DocSearchLLMClarifyOption(label="国六", description="3份·国六平台", file_ids=["1", "3"]),
                DocSearchLLMClarifyOption(label="国五", description="2份·国五平台", file_ids=["2"]),
            ],
        )


class FakeConfigService:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


class SummarySearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        return {
            "query": query,
            "results": [
                {
                    "file_id": "top-1",
                    "filename": "东风天锦国六整车电路图",
                    "brand": "东风",
                    "series": "天锦",
                    "model": "KR",
                    "doc_types": ["整车电路图"],
                    "pic_folder_url": "https://example.com/top-1",
                    "score": 0.92,
                },
                {
                    "file_id": "top-2",
                    "filename": "东风天锦国五整车电路图",
                    "brand": "东风",
                    "series": "天锦",
                    "model": "KR",
                    "doc_types": ["整车电路图"],
                    "score": 0.87,
                },
            ],
            "preprocessing": {"entities": {"brand": ["东风"], "series": ["天锦"], "doc_type": ["整车电路图"]}},
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


class TopResultClarifySearchEngine:
    def __init__(self, _db):
        pass

    def search(self, query: str, top_k: int = 20, lexical_top_k: int = 200, use_vector: bool = False):
        results = []
        for idx in range(6):
            results.append(
                {
                    "file_id": f"top-{idx + 1}",
                    "filename": f"东风天锦配置版整车电路图_{idx + 1}",
                    "brand": "东风",
                    "series": "天锦",
                    "model": "KR",
                    "doc_types": ["整车电路图"],
                    "pic_folder_url": f"https://example.com/top-{idx + 1}" if idx == 0 else None,
                    "score": 0.92 - idx * 0.01,
                }
            )
        return {
            "query": query,
            "results": results,
            "preprocessing": {"entities": {"brand": ["东风"], "series": ["天锦"], "doc_type": ["整车电路图"]}},
            "search_method": "lexical_only",
            "search_time_ms": 6.0,
        }


def test_doc_search_service_builds_summary_fields():
    dimension_service = FakeDimensionService(
        doc_type_patterns={"整车电路图": ["整车图", "整车电路图", "整车线束图"]},
    )
    service = build_service(
        SummarySearchEngine,
        dimension_service=dimension_service,
    )

    result = service.execute(DocSearchRequest(query="东风天锦整车图", top_k=10))

    assert result.summary_query == "东风 天锦 整车图"
    assert result.summary == "找到 2 个「东风 天锦 整车图」相关文档"
    assert result.result_summary is not None
    assert result.result_summary.question == "东风天锦整车图（东风 天锦）"
    assert "东风天锦国六整车电路图" in result.result_summary.preview


def test_doc_search_service_builds_top_result_quick_confirm_payload():
    service = build_service(TopResultClarifySearchEngine)
    search_result = service.execute(DocSearchRequest(query="东风天锦整车图", top_k=10))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing=search_result.preprocessing,
            existing_filters=search_result.applied_filters,
            query=search_result.original_query,
            validity=search_result.validity.model_dump(mode="json"),
        )
    )

    assert analysis.context is not None
    assert analysis.context.top_result is not None
    assert analysis.context.top_result.file_id == "top-1"
    assert analysis.context.top_result.selection_payload.filters == {
        "brand": "东风",
        "series": "天锦",
        "doc_type": "整车电路图",
    }
    assert analysis.context.top_result.selection_payload.file_ids == ["top-1"]


def test_doc_search_service_quick_confirm_selection_returns_only_target_file():
    service = build_service(TopResultClarifySearchEngine)

    result = service.execute(
        DocSearchRequest(
            query="东风天锦整车图",
            top_k=10,
            selection_payload={
                "filters": {"brand": "东风", "series": "天锦", "doc_type": "整车电路图"},
                "file_ids": ["top-1"],
            },
        )
    )

    assert result.total == 1
    assert [item["file_id"] for item in result.results] == ["top-1"]
    assert result.applied_filters == {
        "brand": "东风",
        "series": "天锦",
        "doc_type": "整车电路图",
    }


def test_doc_search_service_uses_llm_smart_after_rule_clarify_gives_up():
    service = build_service(
        FakeAmbiguousSearchEngine,
        clarify_service=FakeNoRuleClarifyService(),
        llm_clarify_service=FakeLLMClarifyService(),
        config_service=FakeConfigService({"llm_clarify_min_results": 5}),
    )
    search_result = service.execute(DocSearchRequest(query="东风电路图", top_k=20))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing=search_result.preprocessing,
            existing_filters=search_result.applied_filters,
            query=search_result.original_query,
            validity=search_result.validity.model_dump(mode="json"),
        )
    )

    assert analysis.need_clarify is True
    assert analysis.source == "llm"
    assert analysis.facet == "llm_smart"
    assert analysis.reason == "llm_smart_clarify"
    assert analysis.results_count == 3
    assert analysis.options[0].label == "国六"
    assert analysis.options[0].description == "3份·国六平台"
    assert analysis.options[0].selection_payload.filters == {"brand": "东风"}
    assert analysis.options[0].selection_payload.file_ids == ["1", "3"]
    assert analysis.context is not None
    assert analysis.context.results_count == 3


def test_doc_search_service_builds_non_empty_selection_payload_for_other_rule_option():
    service = build_service(
        FakeAmbiguousSearchEngine,
        clarify_service=FakeClarifyServiceWithOther(),
    )
    search_result = service.execute(DocSearchRequest(query="东风电路图", top_k=20))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing=search_result.preprocessing,
            existing_filters=search_result.applied_filters,
            query=search_result.original_query,
            validity=search_result.validity.model_dump(mode="json"),
        )
    )

    assert analysis.need_clarify is True
    other_option = next(option for option in analysis.options if option.label == "其他")
    assert other_option.selection_payload.filters == {"brand": "东风"}
    assert other_option.selection_payload.file_ids == ["3", "4", "5", "6"]


def test_doc_search_service_builds_other_rule_option_from_remaining_duplicate_matches():
    service = build_service(
        RepeatedSeriesAmbiguousSearchEngine,
        clarify_service=FakeClarifyServiceWithOther(),
    )
    search_result = service.execute(DocSearchRequest(query="东风电路图", top_k=20))

    analysis = asyncio.run(
        service.analyze_ambiguity(
            results=search_result.results,
            preprocessing=search_result.preprocessing,
            existing_filters=search_result.applied_filters,
            query=search_result.original_query,
            validity=search_result.validity.model_dump(mode="json"),
        )
    )

    assert analysis.need_clarify is True
    other_option = next(option for option in analysis.options if option.label == "其他")
    assert other_option.selection_payload.filters == {"brand": "东风"}
    assert other_option.selection_payload.file_ids == ["3", "4", "5", "6"]


def test_llm_smart_reuses_agent_instance(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        create_calls = 0

        def __init__(self, **kwargs):
            FakeAgent.create_calls += 1
            self.kwargs = kwargs

        async def run(self, *, user_prompt):
            assert "东风电路图" in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请选择资料版本：",
                    dimension="variant",
                    options=[
                        types.SimpleNamespace(label="国六", description="2份", doc_indices=[0, 1]),
                        types.SimpleNamespace(label="国五", description="1份", doc_indices=[2]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService({"llm_clarify_min_results": 0}),
        model_override="fake-model",
    )
    results = [
        {"file_id": "1", "filename": "东风天锦国六电路图"},
        {"file_id": "2", "filename": "东风天锦国六维修手册"},
        {"file_id": "3", "filename": "东风天锦国五电路图"},
    ]

    first = asyncio.run(
        service.analyze(
            results=results,
            query="东风电路图",
            existing_filters={},
            user_intent_entities=None,
        )
    )
    second = asyncio.run(
        service.analyze(
            results=results,
            query="东风电路图",
            existing_filters={},
            user_intent_entities=None,
        )
    )

    assert first is not None
    assert second is not None
    assert FakeAgent.create_calls == 1


def test_llm_smart_scopes_prompt_results_by_explicit_specific_doc_type(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, *, user_prompt):
            assert "东风天龙D310_国四_整车电路图" in user_prompt
            assert "东风天龙D310_国五_整车电路图" in user_prompt
            assert "东风天龙D310_仪表系统电路图" not in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请问您需要查找哪个排放阶段的整车电路图？",
                    dimension="emissions",
                    options=[
                        types.SimpleNamespace(label="国四", description="1份", doc_indices=[0]),
                        types.SimpleNamespace(label="国五", description="1份", doc_indices=[1]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService({"llm_clarify_min_results": 0}),
        model_override="fake-model",
    )
    results = [
        {"file_id": "whole-1", "filename": "东风天龙D310_国四_整车电路图", "doc_types": ["整车电路图"]},
        {"file_id": "part-1", "filename": "东风天龙D310_仪表系统电路图", "doc_types": ["电路图"]},
        {"file_id": "whole-2", "filename": "东风天龙D310_国五_整车电路图", "doc_types": ["整车电路图"]},
    ]

    analysis = asyncio.run(
        service.analyze(
            results=results,
            query="东风天龙D310_整车电路图",
            existing_filters={},
            user_intent_entities={"doc_type": ["电路图", "整车电路图"]},
        )
    )

    assert analysis is not None
    assert analysis.options[0].file_ids == ["whole-1"]
    assert analysis.options[1].file_ids == ["whole-2"]


def test_llm_smart_uses_scoped_results_even_when_below_global_minimum(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, *, user_prompt):
            assert "国四_整车电路图" in user_prompt
            assert "国五_整车电路图" in user_prompt
            assert "液压系统电路图" not in user_prompt
            assert "仪表系统电路图" not in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请问您需要查找哪个排放阶段的整车电路图？",
                    dimension="emissions",
                    options=[
                        types.SimpleNamespace(label="国四", description="1份", doc_indices=[0]),
                        types.SimpleNamespace(label="国五", description="1份", doc_indices=[1]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService({"llm_clarify_min_results": 5}),
        model_override="fake-model",
    )
    results = [
        {"file_id": "whole-1", "filename": "东风天龙D310_国四_整车电路图", "doc_types": ["整车电路图"]},
        {"file_id": "part-1", "filename": "东风天龙D310_仪表系统电路图", "doc_types": ["电路图"]},
        {"file_id": "whole-2", "filename": "东风天龙D310_国五_整车电路图", "doc_types": ["整车电路图"]},
        {"file_id": "part-2", "filename": "东风天龙D310_液压系统电路图", "doc_types": ["电路图"]},
        {"file_id": "part-3", "filename": "东风天龙D310_ABS电路图", "doc_types": ["电路图"]},
        {"file_id": "part-4", "filename": "东风天龙D310_空调电路图", "doc_types": ["电路图"]},
    ]

    analysis = asyncio.run(
        service.analyze(
            results=results,
            query="东风天龙D310_整车电路图",
            existing_filters={},
            user_intent_entities={"doc_type": ["电路图", "整车电路图"]},
        )
    )

    assert analysis is not None
    assert analysis.options[0].file_ids == ["whole-1"]
    assert analysis.options[1].file_ids == ["whole-2"]


def test_llm_smart_scopes_prompt_results_by_explicit_series_model_and_doc_type(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, *, user_prompt):
            assert "东风天龙D310_国四_整车电路图" in user_prompt
            assert "东风天龙D310_国五_整车电路图" in user_prompt
            assert "东风天龙D320_国六_整车电路图" not in user_prompt
            assert "东风天龙D760_国六_整车电路图" not in user_prompt
            assert "东风天龙D310_仪表系统电路图" not in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请问您需要查找哪个排放阶段的整车电路图？",
                    dimension="emissions",
                    options=[
                        types.SimpleNamespace(label="国四", description="1份", doc_indices=[0]),
                        types.SimpleNamespace(label="国五", description="1份", doc_indices=[1]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService({"llm_clarify_min_results": 0}),
        model_override="fake-model",
    )
    results = [
        {"file_id": "d310-whole-1", "filename": "东风天龙D310_国四_整车电路图", "series": "天龙", "doc_types": ["整车电路图"]},
        {"file_id": "d320-whole", "filename": "东风天龙D320_国六_整车电路图", "series": "天龙", "doc_types": ["整车电路图"]},
        {"file_id": "d310-whole-2", "filename": "东风天龙D310_国五_整车电路图", "series": "天龙", "doc_types": ["整车电路图"]},
        {"file_id": "d760-whole", "filename": "东风天龙D760_国六_整车电路图", "series": "天龙", "doc_types": ["整车电路图"]},
        {"file_id": "d310-part", "filename": "东风天龙D310_仪表系统电路图", "series": "天龙", "doc_types": ["电路图"]},
    ]

    analysis = asyncio.run(
        service.analyze(
            results=results,
            query="东风天龙D310_整车电路图",
            existing_filters={},
            user_intent_entities={"series": ["天龙"], "model": ["D310"], "doc_type": ["整车电路图"]},
        )
    )

    assert analysis is not None
    assert analysis.options[0].file_ids == ["d310-whole-1"]
    assert analysis.options[1].file_ids == ["d310-whole-2"]


def test_llm_smart_assigns_remaining_docs_to_other_like_option(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, *, user_prompt):
            assert "东风天龙D310_国四_整车电路图" in user_prompt
            assert "东风天龙D310_国五_整车电路图" in user_prompt
            assert "东风天龙D760_国六_整车电路图" in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请选择资料范围：",
                    dimension="variant",
                    options=[
                        types.SimpleNamespace(label="国四整车电路图", description="1份", doc_indices=[0]),
                        types.SimpleNamespace(label="国五整车电路图", description="1份", doc_indices=[1]),
                        types.SimpleNamespace(label="其他系列资料", description="1份", doc_indices=[]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService({"llm_clarify_min_results": 0}),
        model_override="fake-model",
    )
    results = [
        {"file_id": "whole-1", "filename": "东风天龙D310_国四_整车电路图", "doc_types": ["整车电路图"]},
        {"file_id": "whole-2", "filename": "东风天龙D310_国五_整车电路图", "doc_types": ["整车电路图"]},
        {"file_id": "other-1", "filename": "东风天龙D760_国六_整车电路图", "doc_types": ["整车电路图"]},
    ]

    analysis = asyncio.run(
        service.analyze(
            results=results,
            query="东风天龙D310_整车电路图",
            existing_filters={},
            user_intent_entities={"series": ["天龙"], "doc_type": ["整车电路图"]},
        )
    )

    assert analysis is not None
    other_option = next(option for option in analysis.options if option.label == "其他系列资料")
    assert other_option.file_ids == ["other-1"]


def test_llm_smart_prefixes_legacy_openrouter_model(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        last_model = None

        def __init__(self, **kwargs):
            FakeAgent.last_model = kwargs["model"]

        async def run(self, *, user_prompt):
            assert "东风电路图" in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请选择资料版本：",
                    dimension="variant",
                    options=[
                        types.SimpleNamespace(label="国六", description="2份", doc_indices=[0, 1]),
                        types.SimpleNamespace(label="国五", description="1份", doc_indices=[2]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService(
            {
                "llm_clarify_min_results": 0,
                "openrouter_clarify_model": "google/gemini-3.1-flash-lite-preview",
            }
        ),
    )
    results = [
        {"file_id": "1", "filename": "东风天锦国六电路图"},
        {"file_id": "2", "filename": "东风天锦国六维修手册"},
        {"file_id": "3", "filename": "东风天锦国五电路图"},
    ]

    response = asyncio.run(
        service.analyze(
            results=results,
            query="东风电路图",
            existing_filters={},
            user_intent_entities=None,
        )
    )

    assert response is not None
    assert FakeAgent.last_model == "openrouter:google/gemini-3.1-flash-lite-preview"


def test_llm_smart_prefers_google_provider_when_gemini_key_present(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        last_model = None

        def __init__(self, **kwargs):
            FakeAgent.last_model = kwargs["model"]

        async def run(self, *, user_prompt):
            assert "东风电路图" in user_prompt
            return types.SimpleNamespace(
                output=types.SimpleNamespace(
                    question="请选择资料版本：",
                    dimension="variant",
                    options=[
                        types.SimpleNamespace(label="国六", description="2份", doc_indices=[0, 1]),
                        types.SimpleNamespace(label="国五", description="1份", doc_indices=[2]),
                    ],
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    service = PydanticAIDocSearchLLMClarifyService(
        config_service=FakeConfigService(
            {
                "llm_clarify_min_results": 0,
                "openrouter_clarify_model": "google/gemini-3.1-flash-lite-preview",
            }
        ),
    )
    results = [
        {"file_id": "1", "filename": "东风天锦国六电路图"},
        {"file_id": "2", "filename": "东风天锦国六维修手册"},
        {"file_id": "3", "filename": "东风天锦国五电路图"},
    ]

    response = asyncio.run(
        service.analyze(
            results=results,
            query="东风电路图",
            existing_filters={},
            user_intent_entities=None,
        )
    )

    assert response is not None
    assert FakeAgent.last_model == "google-gla:gemini-3.1-flash-lite-preview"


def test_doc_search_query_planner_builds_multiple_search_like_queries(monkeypatch):
    import pydantic_ai
    import pydantic_ai.settings as pydantic_ai_settings

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    class FakeModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        last_model = None
        last_prompt = None

        def __init__(self, **kwargs):
            FakeAgent.last_model = kwargs["model"]

        async def run(self, *, user_prompt):
            FakeAgent.last_prompt = user_prompt
            return types.SimpleNamespace(
                output=DocSearchQueryPlan(
                    primary_query="云内 ECU电路图 计量单元 两线",
                    queries=[
                        DocSearchPlannedQuery(
                            query="云内 ECU电路图 计量单元 两线",
                            intent="doc_search",
                            confidence=0.91,
                        ),
                        DocSearchPlannedQuery(
                            query="云内 电脑板针脚定义 计量单元",
                            intent="doc_search",
                            confidence=0.83,
                        ),
                        DocSearchPlannedQuery(
                            query="云内 发动机电路图 ECU",
                            intent="doc_search",
                            confidence=0.72,
                        ),
                    ],
                    rationale="优先围绕品牌、ECU和资料类型组合搜索词。",
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(pydantic_ai_settings, "ModelSettings", FakeModelSettings)

    planner = PydanticAIDocSearchQueryPlanner(
        config_service=FakeConfigService(
            {
                "openrouter_clarify_model": "google/gemini-3.1-flash-lite-preview",
            }
        )
    )

    plan = asyncio.run(
        planner.plan(
            query="这个板子是哪个，带计量单元2线的云内",
            image_evidence="场景=document_hint；摘要=图片中疑似 ECU 板卡；建议查询=云内 ECU电路图；云内 电脑板针脚定义",
            known_slots="品牌=云内；部件=ECU",
        )
    )

    assert plan is not None
    assert plan.primary_query == "云内 ECU电路图 计量单元 两线"
    assert [item.query for item in plan.queries] == [
        "云内 ECU电路图 计量单元 两线",
        "云内 电脑板针脚定义 计量单元",
        "云内 发动机电路图 ECU",
    ]
    assert FakeAgent.last_model == "openrouter:google/gemini-3.1-flash-lite-preview"
    assert "这个板子是哪个" in FakeAgent.last_prompt
    assert "图片证据摘要" in FakeAgent.last_prompt
