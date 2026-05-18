import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.adapters.legacy_doc_search_adapter import LegacyDocSearchAdapter
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.doc_search_cache_store import DocSearchCacheStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.tools.registry import build_default_tool_registry
from app.legacy.models.admin_models import AdminUser
from app.legacy.models.database import Base, get_db
from app.legacy.services.token_identity_service import TokenIdentityRequestError, TokenIdentityTimeoutError
from app.legacy.utils.auth import get_password_hash, verify_password
from app.main import create_app
from app.schemas.chat import ChatResponse


class FakeTokenIdentityService:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    async def resolve_user_id(self, token):
        return self.mapping.get(token)


class RecordingAgentService:
    def __init__(self):
        self.last_runtime_deps = None

    async def process(self, request, runtime_deps=None):
        self.last_runtime_deps = runtime_deps
        return ChatResponse(
            type="message",
            content="ok",
            session_id=request.session_id or "sess_req_scope",
            business="AGENT_LOOP",
        )


class FakeDetailedTokenIdentityService:
    def __init__(self, error):
        self._error = error

    async def resolve_user_id(self, token):
        raise self._error

    async def validate_token(self, token):
        raise self._error


class FakeSession:
    def close(self):
        return None


class FailIfCalledSearchEngine:
    def __init__(self, _db):
        raise AssertionError("mysql fallback should not be used when app_token is present")


class FakeGgzjSearchClient:
    def __init__(self):
        self.calls = 0

    async def search(self, query: str, app_token: str):
        self.calls += 1
        assert query == "东风电路图"
        assert app_token == "valid-token"
        return [{"sn": 101, "dataNameWs": "东风天锦电路图"}, {"sn": 102, "dataNameWs": "解放J6电路图"}]


class FakeGgzjResultAdapter:
    def adapt_list(self, raw_items, query: str):
        assert len(raw_items) == 2
        assert query == "东风电路图"
        return (
            [
                {
                    "file_id": "ggzj_101",
                    "filename": "东风天锦电路图",
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.9,
                    "ggzj_sn": 101,
                    "ggzj_data_type": 2,
                    "ggzj_file_no": "FN-101",
                    "ggzj_file_type": "共轨之家图文",
                },
                {
                    "file_id": "ggzj_102",
                    "filename": "解放J6电路图",
                    "brand": "解放",
                    "series": "J6",
                    "score": 0.7,
                    "ggzj_sn": 102,
                    "ggzj_data_type": 3,
                    "ggzj_file_no": "FN-102",
                    "ggzj_file_type": "电路图",
                },
            ],
            {"entities": {"brand": ["东风"]}, "original_query": query},
        )


class FakeGgzjSearchClientForSearchApi:
    async def search(self, query: str, app_token: str):
        assert query == "东风电路图"
        assert app_token == "valid-token"
        return [{"sn": 101, "dataNameWs": "东风天锦电路图"}, {"sn": 102, "dataNameWs": "解放J6电路图"}]


class FakeGgzjResultAdapterForSearchApi:
    def adapt_list(self, raw_items, query: str):
        assert len(raw_items) == 2
        assert query == "东风电路图"
        return (
            [
                {
                    "file_id": "ggzj_101",
                    "filename": "东风天锦电路图",
                    "brand": "东风",
                    "series": "天锦",
                    "score": 0.9,
                    "ggzj_sn": 101,
                    "ggzj_data_type": 2,
                    "ggzj_file_no": "FN-101",
                    "ggzj_file_type": "共轨之家图文",
                    "pic_folder_url": None,
                }
            ],
            {"entities": {"brand": ["东风"]}, "original_query": query},
        )


class DictCacheStore:
    def __init__(self):
        self.data = {}

    def load(self, key):
        return self.data.get(key)

    def save(self, key, payload):
        self.data[key] = payload
        return True


class FakeResolver:
    async def resolve(self, *, sn, data_type, file_no, file_type, app_token):
        assert sn == 123
        assert data_type == 2
        assert file_no == "FN-1"
        assert file_type == "共轨之家图文"
        assert app_token == "valid-token"
        return {"url": "https://example.com/file.pdf", "url_type": "pdf_loader"}


def _build_runtime_deps(tmp_path, *, token_mapping=None):
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        token_identity_service=FakeTokenIdentityService(token_mapping or {}),
        ggzj_file_url_resolver=FakeResolver(),
    )


def test_chat_api_compat_path_builds_request_scoped_runtime_deps(tmp_path):
    app = create_app()
    recording_service = RecordingAgentService()
    runtime_deps = _build_runtime_deps(tmp_path, token_mapping={"valid-token": 42})

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        app.state.agent_service = recording_service
        response = client.post(
            "/chat/api/chat/completions",
            json={"message": "hello"},
            headers={"x-app-token": "valid-token"},
        )

    assert response.status_code == 200
    assert recording_service.last_runtime_deps is not None
    assert recording_service.last_runtime_deps.app_token == "valid-token"
    assert recording_service.last_runtime_deps.user_id == 42
    assert recording_service.last_runtime_deps.enforce_external_doc_search is True
    assert runtime_deps.app_token is None
    assert runtime_deps.user_id is None


def test_chat_api_compat_path_accepts_query_token(tmp_path):
    app = create_app()
    recording_service = RecordingAgentService()
    runtime_deps = _build_runtime_deps(tmp_path, token_mapping={"valid-token": 42})

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        app.state.agent_service = recording_service
        response = client.post(
            "/chat/api/chat/completions?token=valid-token",
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert recording_service.last_runtime_deps is not None
    assert recording_service.last_runtime_deps.app_token == "valid-token"
    assert recording_service.last_runtime_deps.user_id == 42
    assert recording_service.last_runtime_deps.enforce_external_doc_search is True


def test_chat_api_keeps_raw_app_token_when_user_id_cannot_be_resolved(tmp_path):
    app = create_app()
    runtime_deps = _build_runtime_deps(tmp_path, token_mapping={"valid-token": 42})
    recording_service = RecordingAgentService()

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        app.state.agent_service = recording_service
        response = client.post(
            "/chat/api/chat/completions",
            json={"message": "hello"},
            headers={"x-app-token": "expired-token"},
        )

    assert response.status_code == 200
    assert recording_service.last_runtime_deps is not None
    assert recording_service.last_runtime_deps.app_token == "expired-token"
    assert recording_service.last_runtime_deps.user_id is None


def test_chat_api_tolerates_timeout_when_resolving_user_identity(tmp_path):
    app = create_app()
    runtime_deps = _build_runtime_deps(tmp_path)
    runtime_deps.token_identity_service = FakeDetailedTokenIdentityService(
        TokenIdentityTimeoutError("上游服务响应超时")
    )
    recording_service = RecordingAgentService()

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        app.state.agent_service = recording_service
        response = client.post(
            "/chat/api/chat/completions",
            json={"message": "hello"},
            headers={"x-app-token": "valid-token"},
        )

    assert response.status_code == 200
    assert recording_service.last_runtime_deps is not None
    assert recording_service.last_runtime_deps.app_token == "valid-token"
    assert recording_service.last_runtime_deps.user_id is None


def test_chat_api_tolerates_proxy_error_when_resolving_user_identity(tmp_path):
    app = create_app()
    runtime_deps = _build_runtime_deps(tmp_path)
    runtime_deps.token_identity_service = FakeDetailedTokenIdentityService(
        TokenIdentityRequestError("代理请求失败: boom")
    )
    recording_service = RecordingAgentService()

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        app.state.agent_service = recording_service
        response = client.post(
            "/chat/api/chat/completions",
            json={"message": "hello"},
            headers={"x-app-token": "valid-token"},
        )

    assert response.status_code == 200
    assert recording_service.last_runtime_deps is not None
    assert recording_service.last_runtime_deps.app_token == "valid-token"
    assert recording_service.last_runtime_deps.user_id is None


def test_legacy_doc_search_adapter_uses_ggzj_and_cache_when_app_token_present(tmp_path):
    client = FakeGgzjSearchClient()
    cache_store = DictCacheStore()
    deps = AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        db_session_factory=lambda: FakeSession(),
        search_engine_factory=FailIfCalledSearchEngine,
        app_token="valid-token",
        ggzj_search_client=client,
        ggzj_result_adapter=FakeGgzjResultAdapter(),
        doc_search_cache_store=cache_store,
    )
    adapter = LegacyDocSearchAdapter(deps)

    first = asyncio.run(adapter.search("东风电路图", top_k=20))
    second = asyncio.run(adapter.search("东风电路图", filters={"brand": "东风"}, top_k=20))

    assert first["status"] == "ok"
    assert first["data"]["total"] == 1
    assert first["data"]["results"][0]["file_id"] == "ggzj_101"
    assert second["status"] == "ok"
    assert second["data"]["total"] == 1
    assert client.calls == 1
    assert len(cache_store.data) == 1


def test_legacy_doc_search_adapter_rejects_missing_token_when_external_search_enforced(tmp_path):
    deps = AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        db_session_factory=lambda: FakeSession(),
        search_engine_factory=FailIfCalledSearchEngine,
        enforce_external_doc_search=True,
    )
    adapter = LegacyDocSearchAdapter(deps)

    result = asyncio.run(adapter.search("东风电路图", top_k=20))

    assert result["status"] == "failed"
    assert result["data"]["error_code"] == "TOKEN_REQUIRED"
    assert result["data"]["message"] == "未登录，请重新进入"


def test_ggzj_file_url_endpoint_compat_path(tmp_path):
    app = create_app()
    runtime_deps = _build_runtime_deps(tmp_path)

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        response = client.post(
            "/chat/api/ggzj/file-url",
            json={"sn": 123, "data_type": 2, "file_no": "FN-1", "file_type": "共轨之家图文"},
            headers={"x-app-token": "valid-token"},
        )

    assert response.status_code == 200
    assert response.json()["url_type"] == "pdf_loader"


def test_ggzj_file_url_endpoint_accepts_query_token(tmp_path):
    app = create_app()
    runtime_deps = _build_runtime_deps(tmp_path)

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        response = client.post(
            "/chat/api/ggzj/file-url?token=valid-token",
            json={"sn": 123, "data_type": 2, "file_no": "FN-1", "file_type": "共轨之家图文"},
        )

    assert response.status_code == 200
    assert response.json()["url_type"] == "pdf_loader"


def test_search_api_rejects_missing_token(tmp_path):
    app = create_app()
    runtime_deps = _build_runtime_deps(tmp_path)

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        response = client.post(
            "/chat/api/search",
            json={"query": "东风电路图", "filters": {}, "limit": 20},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录，请重新进入"


def test_search_api_exposes_ggzj_open_link_fields(tmp_path):
    app = create_app()
    runtime_deps = AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
        db_session_factory=lambda: FakeSession(),
        token_identity_service=FakeTokenIdentityService({"valid-token": 42}),
        ggzj_search_client=FakeGgzjSearchClientForSearchApi(),
        ggzj_result_adapter=FakeGgzjResultAdapterForSearchApi(),
    )

    with TestClient(app) as client:
        app.state.runtime_deps = runtime_deps
        response = client.post(
            "/chat/api/search?token=valid-token",
            json={"query": "东风电路图", "filters": {}, "limit": 20},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["file_id"] == "ggzj_101"
    assert body["results"][0]["ggzj_sn"] == 101
    assert body["results"][0]["ggzj_data_type"] == 2
    assert body["results"][0]["ggzj_file_no"] == "FN-101"
    assert body["results"][0]["ggzj_file_type"] == "共轨之家图文"


def test_admin_auth_login_me_and_change_password():
    app = create_app()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    AdminUser.__table__.create(bind=engine)

    session = TestingSessionLocal()
    admin = AdminUser(
        username="admin",
        password_hash=get_password_hash("secret123"),
        role="admin",
        is_active=True,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    session.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        bad_login = client.post("/chat/api/admin/auth/login", json={"username": "admin", "password": "wrong"})
        assert bad_login.status_code == 401

        login = client.post("/chat/api/admin/auth/login", json={"username": "admin", "password": "secret123"})
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = client.get("/chat/api/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "admin"

        change = client.put(
            "/chat/api/admin/auth/password",
            json={"old_password": "secret123", "new_password": "newsecret456"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert change.status_code == 200

    verify_session = TestingSessionLocal()
    refreshed = verify_session.query(AdminUser).filter(AdminUser.username == "admin").first()
    assert refreshed is not None
    assert verify_password("newsecret456", refreshed.password_hash) is True
    verify_session.close()
