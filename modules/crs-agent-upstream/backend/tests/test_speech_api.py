from fastapi.testclient import TestClient

from app.main import create_app


def test_aliyun_speech_token_endpoint_returns_structured_unavailable_response():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/chat/api/speech/aliyun/token")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "error" in body
