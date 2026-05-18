from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import legacy_proxy as legacy_proxy_module
from app.legacy.services.token_identity_service import (
    TokenIdentityRequestError,
    TokenIdentityTimeoutError,
    TokenValidationResult,
)
from app.main import create_app


class FakeValidateTokenService:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.last_token = None

    async def validate_token(self, token):
        self.last_token = token
        if self._error is not None:
            raise self._error
        return self._result


def test_legacy_auth_enabled_compat_path(monkeypatch):
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/chat/api/legacy/auth-enabled")

    assert response.status_code == 200
    assert response.json() == {"enabled": True}


def test_legacy_validate_token_compat_path_returns_user_id():
    app = create_app()
    service = FakeValidateTokenService(result=TokenValidationResult(valid=True, user_id=842728, message="ok"))

    with TestClient(app) as client:
        app.state.runtime_deps = SimpleNamespace(token_identity_service=service)
        response = client.post("/chat/api/legacy/validate-token", json={"token": "valid-token"})

    assert response.status_code == 200
    assert response.json() == {"valid": True, "userId": 842728}
    assert service.last_token == "valid-token"


def test_legacy_validate_token_compat_path_returns_invalid_message():
    app = create_app()
    service = FakeValidateTokenService(result=TokenValidationResult(valid=False, message="登录已失效"))

    with TestClient(app) as client:
        app.state.runtime_deps = SimpleNamespace(token_identity_service=service)
        response = client.post("/chat/api/legacy/validate-token", json={"token": "expired-token"})

    assert response.status_code == 200
    assert response.json() == {"valid": False, "message": "登录已失效"}


def test_legacy_validate_token_compat_path_returns_timeout():
    app = create_app()
    service = FakeValidateTokenService(error=TokenIdentityTimeoutError("上游服务响应超时"))

    with TestClient(app) as client:
        app.state.runtime_deps = SimpleNamespace(token_identity_service=service)
        response = client.post("/chat/api/legacy/validate-token", json={"token": "slow-token"})

    assert response.status_code == 504
    assert response.json()["detail"] == "上游服务响应超时"


def test_legacy_validate_token_compat_path_returns_proxy_error():
    app = create_app()
    service = FakeValidateTokenService(error=TokenIdentityRequestError("代理请求失败: boom"))

    with TestClient(app) as client:
        app.state.runtime_deps = SimpleNamespace(token_identity_service=service)
        response = client.post("/chat/api/legacy/validate-token", json={"token": "broken-token"})

    assert response.status_code == 502
    assert response.json()["detail"] == "代理请求失败: boom"


def test_legacy_extract_token_compat_path_accepts_x_app_token():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/chat/api/legacy/extract-token", headers={"x-app-token": "header-token"})

    assert response.status_code == 200
    assert response.json() == {"token": "header-token"}


def test_legacy_token_diagnose_compat_path_includes_sources():
    app = create_app()
    long_header_token = "header-token-value-1234567890"
    long_cookie_token = "cookie-token-value-1234567890"

    with TestClient(app) as client:
        client.cookies.set("APP_TOKEN", long_cookie_token)
        response = client.get(
            "/chat/api/legacy/token-diagnose?token=query-token",
            headers={"x-app-token": long_header_token, "authorization": "Bearer abc"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query_params"]["token"] == "query-token"
    assert body["token_headers"]["x-app-token"] == "header-token-value-1..."
    assert body["token_headers"]["authorization"] == "Bearer abc"
    assert body["cookies"]["APP_TOKEN"] == "cookie-token-value-1..."
