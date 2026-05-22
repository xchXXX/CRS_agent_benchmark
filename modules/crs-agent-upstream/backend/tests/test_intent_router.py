import asyncio
from types import SimpleNamespace

from app.agent.runtime.intent_router import RequestIntentRouter, RoutedIntent


def test_router_fallback_routes_generic_pin_definition_material_to_doc_search():
    router = RequestIntentRouter()

    decision = router.route("仪表显示器针脚定义")

    assert decision.intent == RoutedIntent.DOC_SEARCH
    assert decision.reason == "pin_definition_doc_material"
    assert decision.source == "fallback_rule"


def test_router_fallback_keeps_explicit_pin_definition_query_as_param_query():
    router = RequestIntentRouter()

    decision = router.route("K46 针脚定义")

    assert decision.intent == RoutedIntent.PARAM_QUERY
    assert decision.reason == "parameter_query_keywords"
    assert decision.source == "fallback_rule"


def test_router_fallback_keeps_canh_pin_query_as_param_query():
    router = RequestIntentRouter()

    decision = router.route("CANH 在哪个针脚")

    assert decision.intent == RoutedIntent.PARAM_QUERY
    assert decision.reason == "parameter_query_keywords"
    assert decision.source == "fallback_rule"


def test_router_routes_doc_body_location_search_before_llm(monkeypatch):
    router = RequestIntentRouter(model_override="fake-model")

    class UnexpectedAgent:
        async def run(self, *, user_prompt):
            raise AssertionError("high-confidence doc body search should not call LLM router")

    monkeypatch.setattr(router, "_get_agent", lambda **kwargs: UnexpectedAgent())

    decision = asyncio.run(router.route_async("找东风天锦整车电路图里面BCM的位置"))

    assert decision.intent == RoutedIntent.DOC_SEARCH
    assert decision.reason == "doc_body_search_material"
    assert decision.source == "fallback_rule"
    assert decision.confidence == 0.98


def test_router_keeps_how_to_find_doc_body_location_as_general_chat():
    router = RequestIntentRouter()

    decision = router.route("怎么找东风天锦整车电路图里面BCM的位置")

    assert decision.intent == RoutedIntent.GENERAL_CHAT
    assert decision.reason == "general_question_keywords"


def test_router_async_prefers_llm_for_ecu_data_material_query(monkeypatch):
    router = RequestIntentRouter(model_override="fake-model")

    class FakeAgent:
        async def run(self, *, user_prompt):
            assert "帮我找 EDC17C53 P924 云内发动机电脑版数据" in user_prompt
            return SimpleNamespace(
                output=SimpleNamespace(
                    intent="doc_search",
                    reason="用户明确要拿到资料本体",
                    confidence=0.93,
                )
            )

    monkeypatch.setattr(router, "_get_agent", lambda **kwargs: FakeAgent())

    decision = asyncio.run(router.route_async("帮我找 EDC17C53 P924 云内发动机电脑版数据"))

    assert decision.intent == RoutedIntent.DOC_SEARCH
    assert decision.reason == "用户明确要拿到资料本体"
    assert decision.source == "llm"
    assert decision.confidence == 0.93


def test_router_async_keeps_how_to_find_ecu_data_as_general_chat(monkeypatch):
    router = RequestIntentRouter(model_override="fake-model")

    class FakeAgent:
        async def run(self, *, user_prompt):
            assert "怎样才能找到EDC17C53 P924 云内发动机电脑版数据" in user_prompt
            return SimpleNamespace(
                output=SimpleNamespace(
                    intent="general_chat",
                    reason="这是方法咨询，不是在直接索取资料文件",
                    confidence=0.89,
                )
            )

    monkeypatch.setattr(router, "_get_agent", lambda **kwargs: FakeAgent())

    decision = asyncio.run(router.route_async("怎样才能找到EDC17C53 P924 云内发动机电脑版数据"))

    assert decision.intent == RoutedIntent.GENERAL_CHAT
    assert decision.reason == "这是方法咨询，不是在直接索取资料文件"
    assert decision.source == "llm"
