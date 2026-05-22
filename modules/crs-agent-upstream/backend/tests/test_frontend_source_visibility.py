from fastapi.testclient import TestClient

from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.models.ask_user import AskUserInputType, AskUserQuestion
from app.agent.models.events import AgentEventType, AgentRuntimeEvent
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.tools.registry import build_default_tool_registry
from app.api.frontend_visibility import sanitize_agent_event, sanitize_chat_response
from app.main import create_app
from app.schemas.chat import ChatResponse


class FakeConfigService:
    def __init__(
        self,
        enabled: bool,
        *,
        eruda_enabled: bool = False,
        webview_debug_enabled: bool = False,
    ):
        self._enabled = enabled
        self._eruda_enabled = eruda_enabled
        self._webview_debug_enabled = webview_debug_enabled

    def get(self, key: str, default=None):
        if key == "frontend_source_display_enabled":
            return self._enabled
        if key == "frontend_eruda_enabled":
            return self._eruda_enabled
        if key == "frontend_webview_debug_enabled":
            return self._webview_debug_enabled
        if key == "frontend_webview_debug_url":
            return default
        if key == "frontend_webview_debug_pdf_id":
            return default
        return default


class FakeRepairKnowledgeService:
    def get_source_detail(self, entry_id: str):
        if entry_id != "repair_knowledge_1":
            return None
        return {"id": entry_id, "title": "内部维修经验"}


class StaticResponseAgentService:
    def __init__(self, response: ChatResponse):
        self._response = response

    async def process(self, request, runtime_deps=None):
        del request, runtime_deps
        return self._response

    async def stream(self, request, runtime_deps=None):
        del request, runtime_deps
        yield AgentRuntimeEvent(
            type=AgentEventType.DONE,
            session_id=self._response.session_id,
            metadata={
                "request_id": "req_1",
                "response": self._response.model_dump(mode="json"),
                "full_content": "",
            },
        )

    def handle_stream_abort(self, session_id: str, partial_content: str) -> bool:
        del session_id, partial_content
        return True


def build_runtime_deps(
    tmp_path,
    *,
    source_display_enabled: bool,
    eruda_enabled: bool = False,
    webview_debug_enabled: bool = False,
    repair_knowledge_service=None,
):
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        config_service=FakeConfigService(
            source_display_enabled,
            eruda_enabled=eruda_enabled,
            webview_debug_enabled=webview_debug_enabled,
        ),
        repair_knowledge_service=repair_knowledge_service,
    )


def build_ask_user_response() -> ChatResponse:
    ask_user = AskUserQuestion(
        tool_call_id="ask_user_1",
        question="请补充信息",
        input_type=AskUserInputType.TEXT,
        allow_free_input=True,
        context={
            "scene": "repair_knowledge_followup",
            "source_refs": [{"id": "repair_knowledge_1", "title": "内部维修经验"}],
        },
    )
    return ChatResponse(
        type="ask_user",
        content=ask_user.model_dump(mode="json"),
        session_id="sess_1",
        metadata={
            "repair_knowledge_sources": [{"id": "repair_knowledge_1", "title": "内部维修经验"}],
            "repair_knowledge_primary_title": "内部维修经验",
        },
        ask_user=ask_user,
    )


def build_param_response() -> ChatResponse:
    return ChatResponse(
        type="param_request",
        content={
            "query": "K46 是什么作用",
            "summary": "K46 的针脚定义为信号。",
            "selected_source": {
                "id": "159",
                "title": "EDC17C53针脚电压(12V系统)",
                "ecu_name": "EDC17C53",
                "system_voltage": 12,
            },
            "rows": [],
            "source_refs": [
                {
                    "id": "159",
                    "title": "EDC17C53针脚电压(12V系统)",
                    "relation": "primary",
                    "match_score": 1.0,
                }
            ],
        },
        session_id="sess_1",
        metadata={
            "repair_knowledge_sources": [{"id": "repair_knowledge_1", "title": "内部维修经验"}],
            "repair_knowledge_primary_title": "内部维修经验",
        },
    )


def test_sanitize_chat_response_removes_source_fields_when_disabled(tmp_path):
    deps = build_runtime_deps(tmp_path, source_display_enabled=False)

    response = sanitize_chat_response(build_ask_user_response(), deps)

    assert "repair_knowledge_sources" not in response.metadata
    assert "repair_knowledge_primary_title" not in response.metadata
    assert response.ask_user is not None
    assert "source_refs" not in response.ask_user.context
    assert "source_refs" not in response.content["context"]


def test_sanitize_chat_response_keeps_source_fields_when_enabled(tmp_path):
    deps = build_runtime_deps(tmp_path, source_display_enabled=True)

    response = sanitize_chat_response(build_ask_user_response(), deps)

    assert response.metadata["repair_knowledge_sources"][0]["title"] == "内部维修经验"
    assert response.ask_user is not None
    assert response.ask_user.context["source_refs"][0]["id"] == "repair_knowledge_1"


def test_sanitize_agent_event_removes_nested_source_fields_when_disabled(tmp_path):
    deps = build_runtime_deps(tmp_path, source_display_enabled=False)
    event = AgentRuntimeEvent(
        type=AgentEventType.DONE,
        session_id="sess_1",
        metadata={
            "response": build_param_response().model_dump(mode="json"),
            "full_content": "",
        },
    )

    sanitized = sanitize_agent_event(event, deps)
    response_payload = sanitized.metadata["response"]

    assert "repair_knowledge_sources" not in response_payload["metadata"]
    assert "repair_knowledge_primary_title" not in response_payload["metadata"]
    assert "source_refs" not in response_payload["content"]


def test_chat_completions_hides_sources_at_http_boundary(tmp_path):
    response = build_param_response()
    app = create_app()

    with TestClient(app) as client:
        app.state.runtime_deps = build_runtime_deps(tmp_path, source_display_enabled=False)
        app.state.agent_service = StaticResponseAgentService(response)

        result = client.post("/chat/completions", json={"message": "K46 是什么作用"})

    assert result.status_code == 200
    body = result.json()
    assert "source_refs" not in body["content"]
    assert "repair_knowledge_sources" not in body["metadata"]
    assert "repair_knowledge_primary_title" not in body["metadata"]


def test_source_detail_endpoint_respects_visibility_switch(tmp_path):
    app = create_app()

    with TestClient(app) as client:
        app.state.runtime_deps = build_runtime_deps(
            tmp_path,
            source_display_enabled=False,
            repair_knowledge_service=FakeRepairKnowledgeService(),
        )
        app.state.agent_service = object()

        hidden = client.get("/repair-knowledge/source/repair_knowledge_1")

        app.state.runtime_deps = build_runtime_deps(
            tmp_path,
            source_display_enabled=True,
            repair_knowledge_service=FakeRepairKnowledgeService(),
        )
        shown = client.get("/repair-knowledge/source/repair_knowledge_1")

    assert hidden.status_code == 200
    assert hidden.json() == {"success": False, "message": "来源展示未启用"}
    assert shown.status_code == 200
    assert shown.json()["success"] is True
    assert shown.json()["data"]["title"] == "内部维修经验"


def test_frontend_runtime_config_exposes_eruda_switch(tmp_path):
    app = create_app()

    with TestClient(app) as client:
        app.state.runtime_deps = build_runtime_deps(
            tmp_path,
            source_display_enabled=False,
            eruda_enabled=False,
        )
        disabled = client.get("/chat/api/frontend/runtime-config")

        app.state.runtime_deps = build_runtime_deps(
            tmp_path,
            source_display_enabled=False,
            eruda_enabled=True,
        )
        enabled = client.get("/chat/api/frontend/runtime-config")

    assert disabled.status_code == 200
    disabled_body = disabled.json()
    assert disabled_body["eruda_enabled"] is False
    assert disabled_body["webview_debug_enabled"] is False
    assert disabled_body["webview_debug_viewer_token"] == ""
    assert enabled.status_code == 200
    enabled_body = enabled.json()
    assert enabled_body["eruda_enabled"] is True
    assert enabled_body["webview_debug_enabled"] is False


def test_frontend_runtime_config_exposes_webview_debug_switch(tmp_path):
    app = create_app()

    with TestClient(app) as client:
        app.state.runtime_deps = build_runtime_deps(
            tmp_path,
            source_display_enabled=False,
            webview_debug_enabled=True,
        )
        response = client.get("/chat/api/frontend/runtime-config")

    assert response.status_code == 200
    body = response.json()
    assert body["webview_debug_enabled"] is True
    assert body["webview_debug_url"]
    assert body["webview_debug_viewer_token"]
