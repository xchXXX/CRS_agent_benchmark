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


def test_router_fallback_routes_pin_definition_diagram_with_vehicle_model_to_doc_search():
    router = RequestIntentRouter()

    decision = router.route("老师，麻烦帮忙找下国六红岩杰狮H6 BCM的针脚定义图")

    assert decision.intent == RoutedIntent.DOC_SEARCH
    assert decision.reason == "pin_definition_doc_material"
    assert decision.source == "fallback_rule"


def test_router_fallback_keeps_canh_pin_query_as_param_query():
    router = RequestIntentRouter()

    decision = router.route("CANH 在哪个针脚")

    assert decision.intent == RoutedIntent.PARAM_QUERY
    assert decision.reason == "parameter_query_keywords"
    assert decision.source == "fallback_rule"


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
