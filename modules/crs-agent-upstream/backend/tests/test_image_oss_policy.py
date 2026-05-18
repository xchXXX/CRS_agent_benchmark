import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.tools.registry import build_default_tool_registry
from app.core.config import settings
from app.legacy.models.database import Base, OssImageDeleteJob
from app.legacy.services.oss_image_delete_service import OssImageDeleteService
from app.main import create_app


class FakeConfig:
    def get(self, key, default=None):
        values = {
            "aliyun_oss_image_upload_enabled": True,
            "aliyun_oss_access_key_id": "",
            "aliyun_oss_access_key_secret": "",
            "aliyun_speech_access_key_id": "test-access-id",
            "aliyun_speech_access_key_secret": "test-access-secret",
            "aliyun_oss_bucket_name": "ajie-crs-aidiagosis-image",
            "aliyun_oss_endpoint": "oss-cn-shanghai.aliyuncs.com",
            "aliyun_oss_region": "oss-cn-shanghai",
            "aliyun_oss_image_dir": "chat_images",
            "aliyun_oss_policy_expire_seconds": 900,
            "aliyun_oss_max_image_mb": 8,
            "aliyun_oss_delete_enabled": True,
            "aliyun_oss_delete_token_secret": "delete-secret",
            "aliyun_oss_delete_token_expire_seconds": 604800,
            "aliyun_oss_delete_worker_batch_size": 20,
            "aliyun_oss_delete_retry_base_seconds": 5,
            "aliyun_oss_delete_retry_max_seconds": 21600,
            "aliyun_oss_delete_max_attempts": 0,
        }
        return values.get(key, default)


def _runtime_deps(config_service=None, db_session_factory=None):
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(),
        deferred_state_store=DeferredStateStore(),
        mem0_store=Mem0Store(enabled=False),
        tracer=LoopTracer(),
        config_service=config_service or FakeConfig(),
        db_session_factory=db_session_factory,
        user_id=842728,
    )


def test_image_oss_upload_policy_uses_configured_bucket_and_reuses_aliyun_credentials(monkeypatch):
    monkeypatch.setattr(settings, "aliyun_oss_access_key_id", "")
    monkeypatch.setattr(settings, "aliyun_oss_access_key_secret", "")

    app = create_app()
    app.state.runtime_deps = _runtime_deps()
    client = TestClient(app)

    response = client.post(
        "/chat/api/image/oss-upload-policy",
        json={"filename": "photo.png", "content_type": "image/png", "session_id": "session-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["access_id"] == "test-access-id"
    assert payload["bucket"] == "ajie-crs-aidiagosis-image"
    assert payload["endpoint"] == "oss-cn-shanghai.aliyuncs.com"
    assert payload["host"] == "https://ajie-crs-aidiagosis-image.oss-cn-shanghai.aliyuncs.com"
    assert payload["key"].startswith("chat_images/session-1/")
    assert payload["key"].endswith(".png")
    assert payload["url"].endswith(payload["key"])
    assert payload["policy"]
    assert payload["signature"]
    assert payload["delete_token"]


def test_image_oss_delete_objects_enqueues_persistent_job(monkeypatch):
    monkeypatch.setattr(settings, "aliyun_oss_access_key_id", "")
    monkeypatch.setattr(settings, "aliyun_oss_access_key_secret", "")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    app = create_app()
    app.state.runtime_deps = _runtime_deps(db_session_factory=SessionLocal)
    client = TestClient(app)

    upload_response = client.post(
        "/chat/api/image/oss-upload-policy",
        json={"filename": "photo.jpg", "content_type": "image/jpeg", "session_id": "session-1"},
    )
    upload_payload = upload_response.json()

    response = client.post(
        "/chat/api/image/oss-delete-objects",
        json={
            "session_id": "session-1",
            "reason": "new_search",
            "objects": [
                {
                    "key": upload_payload["key"],
                    "delete_token": upload_payload["delete_token"],
                }
            ],
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1

    db = SessionLocal()
    try:
        job = db.query(OssImageDeleteJob).one()
        assert job.object_key == upload_payload["key"]
        assert job.status == "pending"
        assert job.session_id == "session-1"
        assert job.user_id == 842728
    finally:
        db.close()


def test_oss_image_delete_worker_confirms_jobs_after_session_commit(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    object_key = "chat_images/session-1/20260427/delete-smoke.png"
    db = SessionLocal()
    try:
        db.add(
            OssImageDeleteJob(
                object_key=object_key,
                session_id="session-1",
                user_id=842728,
                reason="new_search",
                status="pending",
                delete_token_hash="x" * 64,
                next_retry_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        db.commit()
    finally:
        db.close()

    service = OssImageDeleteService(session_factory=SessionLocal, config_service=FakeConfig())
    deleted_keys: list[str] = []

    async def fake_delete_and_confirm(key: str) -> None:
        deleted_keys.append(key)

    monkeypatch.setattr(service, "_delete_and_confirm", fake_delete_and_confirm)

    result = asyncio.run(service.process_due_jobs_once())

    assert result == {"processed": 1, "confirmed": 1, "failed": 0}
    assert deleted_keys == [object_key]

    db = SessionLocal()
    try:
        job = db.query(OssImageDeleteJob).one()
        assert job.status == "confirmed"
        assert job.confirmed_at is not None
        assert job.last_error is None
    finally:
        db.close()
