from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.legacy.models.admin_models import AdminUser, SystemConfig
from app.legacy.models.database import (
    Base,
    ChatLog,
    ChatRunEventLog,
    ChatRunLog,
    ChatTaskLog,
    DimFacet,
    DimValue,
    UserFeedback,
    get_db,
)
from app.legacy.services.config_initializer import reconcile_system_configs
from app.legacy.services.dimension_service import dimension_service
from app.legacy.utils.auth import create_access_token, get_password_hash
from app.main import create_app


@pytest.fixture
def admin_client():
    app = create_app()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    session.add(
        AdminUser(
            id=1,
            username="admin",
            password_hash=get_password_hash("secret123"),
            role="admin",
            is_active=True,
        )
    )
    session.add_all(
        [
            SystemConfig(
                config_key="agent_model",
                config_value="test",
                config_type="string",
                category="llm",
                description="主模型",
                is_sensitive=False,
                updated_by="seed",
            ),
            SystemConfig(
                config_key="user_auth_enabled",
                config_value="true",
                config_type="bool",
                category="system",
                description="用户端鉴权开关",
                is_sensitive=False,
                updated_by="seed",
            ),
        ]
    )
    session.add_all(
        [
            DimFacet(
                facet_key="brand",
                facet_name="品牌",
                question="请选择品牌",
                priority=1,
                db_field="brand",
                match_mode="dict",
                specificity=1,
                is_active=True,
            ),
            DimFacet(
                facet_key="series",
                facet_name="系列",
                question="请选择系列",
                priority=2,
                db_field="series",
                parent_facet_key="brand",
                match_mode="dict",
                specificity=2,
                is_active=True,
            ),
        ]
    )
    session.add_all(
        [
            DimValue(
                id=1,
                facet_key="brand",
                value="东风",
                match_patterns="东风,dongfeng",
                sort_order=10,
                is_active=True,
            ),
            DimValue(
                id=2,
                facet_key="series",
                value="天锦",
                match_patterns="天锦,tianjin",
                parent_value_id=1,
                sort_order=9,
                is_active=True,
            ),
        ]
    )
    created_at = datetime.now() - timedelta(hours=1)
    session.add(
        ChatLog(
            id=1,
            request_id="req-1",
            session_id="sess-1",
            user_id=1001,
            user_message="查询东风天锦整车电路图",
            client_type="web",
            request_mode="auto",
            intent_type="DOC_SEARCH",
            intent_confidence=Decimal("0.950"),
            intent_rule="doc_search",
            intent_source="rule",
            response_type="documents",
            response_content='{"items": 3}',
            response_preview="命中 3 条资料",
            clarify_facet="series",
            clarify_options=["天锦", "天龙"],
            has_suggestions=True,
            elapsed_ms=320,
            intent_elapsed_ms=40,
            created_at=created_at,
        )
    )
    session.add(
        UserFeedback(
            id=1,
            request_id="req-1",
            session_id="sess-1",
            rating=8,
            business_type="DOC_SEARCH",
            tags=["结果准确", "返回快"],
            comment="还不错",
            created_at=created_at,
        )
    )
    session.add(
        ChatTaskLog(
            id=1,
            task_id="task-1",
            session_id="sess-1",
            user_id=1001,
            client_type="web",
            root_question="查询东风天锦整车电路图",
            latest_user_message="查询东风天锦整车电路图",
            business_type="DOC_SEARCH",
            task_status="completed",
            end_reason="direct_answer",
            convergence_mode="direct_answer",
            final_response_type="documents",
            final_response_preview="命中 3 条资料",
            final_response_payload={"type": "documents", "content": {"items": 3}},
            ask_user_triggered=False,
            ask_user_count=0,
            run_count=1,
            tool_call_count=2,
            external_tool_call_count=1,
            main_tool_names=["search_documents", "resolve_file_url"],
            has_error=False,
            first_request_id="req-1",
            last_request_id="req-1",
            total_elapsed_ms=320,
            started_at=created_at,
            finished_at=created_at,
            created_at=created_at,
        )
    )
    session.add(
        ChatRunLog(
            id=1,
            run_id="run-1",
            task_id="task-1",
            session_id="sess-1",
            request_id="req-1",
            user_id=1001,
            client_type="web",
            request_mode="auto",
            transport="http",
            sequence_no=1,
            trigger_type="user_message",
            input_message="查询东风天锦整车电路图",
            business_type="DOC_SEARCH",
            run_status="completed",
            end_reason="direct_answer",
            convergence_mode="direct_answer",
            response_type="documents",
            response_preview="命中 3 条资料",
            response_payload={"type": "documents", "content": {"items": 3}},
            response_metadata={
                "runtime": "pydantic_ai",
                "llm": {
                    "model_name": "gpt-4.1-mini",
                    "provider_name": "openai",
                    "call_count": 2,
                    "aggregate_llm_elapsed_ms": 210,
                    "aggregate_first_response_ms": 90,
                    "aggregate_estimated_cost_usd": 0.0012,
                    "aggregate_usage": {
                        "input_tokens": 1500,
                        "output_tokens": 220,
                        "total_tokens": 1720,
                        "reasoning_tokens": 40,
                        "request_count": 2,
                        "tool_call_count": 2,
                        "details": {},
                    },
                },
            },
            ask_user_count=0,
            tool_call_count=2,
            external_tool_call_count=1,
            tool_names=["search_documents", "resolve_file_url"],
            model_provider="openai",
            model_name="gpt-4.1-mini",
            llm_call_count=2,
            llm_elapsed_ms=210,
            llm_first_response_ms=90,
            llm_request_count=2,
            input_token_count=1500,
            output_token_count=220,
            total_token_count=1720,
            reasoning_token_count=40,
            estimated_cost_usd=0.0012,
            has_error=False,
            elapsed_ms=320,
            started_at=created_at,
            finished_at=created_at,
            created_at=created_at,
        )
    )
    session.add_all(
        [
            ChatRunEventLog(
                id=1,
                event_id="evt-1",
                task_id="task-1",
                run_id="run-1",
                request_id="req-1",
                session_id="sess-1",
                sequence_no=1,
                event_type="request_received",
                phase="request",
                summary="收到用户请求",
                detail="查询东风天锦整车电路图",
                payload={"message": "查询东风天锦整车电路图"},
                created_at=created_at,
            ),
            ChatRunEventLog(
                id=2,
                event_id="evt-2",
                task_id="task-1",
                run_id="run-1",
                request_id="req-1",
                session_id="sess-1",
                sequence_no=2,
                event_type="agent_loop_guard_after_tool_call",
                phase="tool",
                tool_name="search_documents",
                summary="工具调用完成 search_documents",
                detail=None,
                payload={"tool_name": "search_documents"},
                created_at=created_at,
            ),
        ]
    )
    session.commit()
    dimension_service.refresh(session)
    session.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token({"sub": "admin", "user_id": 1, "role": "admin"})

    with TestClient(app) as client:
        yield client, token, TestingSessionLocal


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_config_and_dimension_endpoints(admin_client, monkeypatch):
    client, token, session_local = admin_client
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    config_list = client.get("/chat/api/admin/config/list", headers=_auth_headers(token))
    assert config_list.status_code == 200
    assert config_list.json()["llm"][0]["key"] == "agent_model"

    refresh_response = client.post("/chat/api/admin/config/refresh", headers=_auth_headers(token))
    assert refresh_response.status_code == 200

    config_list = client.get("/chat/api/admin/config/list", headers=_auth_headers(token))
    assert config_list.status_code == 200
    llm_keys = {item["key"] for item in config_list.json()["llm"]}
    assert "openrouter_clarify_model" in llm_keys
    assert "llm_clarify_enabled" in llm_keys
    assert "llm_clarify_min_results" in llm_keys
    assert "llm_clarify_max_tokens" in llm_keys
    assert "llm_clarify_temperature" in llm_keys
    assert "llm_clarify_timeout" in llm_keys

    update_response = client.put(
        "/chat/api/admin/config/update",
        json={
            "configs": [
                {"key": "agent_model", "value": "openai/gpt-4.1-mini", "type": "string"},
                {"key": "user_auth_enabled", "value": "false", "type": "bool"},
            ]
        },
        headers=_auth_headers(token),
    )
    assert update_response.status_code == 200
    body = update_response.json()
    assert body["updated"] == ["agent_model"]
    assert body["locked"] == ["user_auth_enabled"]

    verify_session = session_local()
    updated_model = (
        verify_session.query(SystemConfig).filter(SystemConfig.config_key == "agent_model").first()
    )
    locked_auth = (
        verify_session.query(SystemConfig).filter(SystemConfig.config_key == "user_auth_enabled").first()
    )
    assert updated_model.config_value == "openrouter:openai/gpt-4.1-mini"
    assert locked_auth.config_value == "true"
    verify_session.close()

    stats_response = client.get("/chat/api/admin/dimension/stats", headers=_auth_headers(token))
    assert stats_response.status_code == 200
    stats_body = stats_response.json()
    assert stats_body["facet_count"] == 2
    assert stats_body["value_by_facet"]["brand"] == 1
    assert stats_body["cache_loaded"] is True

    create_response = client.post(
        "/chat/api/admin/dimension/values",
        json={
            "facet_key": "series",
            "value": "天龙",
            "match_patterns": "天龙,tianlong",
            "parent_value_id": 1,
            "sort_order": 8,
        },
        headers=_auth_headers(token),
    )
    assert create_response.status_code == 200
    new_value_id = create_response.json()["id"]

    update_value_response = client.put(
        f"/chat/api/admin/dimension/values/{new_value_id}",
        json={"match_patterns": "天龙,tianlong,TL", "sort_order": 12},
        headers=_auth_headers(token),
    )
    assert update_value_response.status_code == 200

    values_response = client.get(
        "/chat/api/admin/dimension/values",
        params={"facet_key": "series"},
        headers=_auth_headers(token),
    )
    assert values_response.status_code == 200
    series_values = values_response.json()
    assert {item["value"] for item in series_values} == {"天锦", "天龙"}

    delete_response = client.delete(
        f"/chat/api/admin/dimension/values/{new_value_id}",
        headers=_auth_headers(token),
    )
    assert delete_response.status_code == 200


def test_admin_logs_endpoints(admin_client):
    client, token, _ = admin_client

    list_response = client.get(
        "/chat/api/admin/logs/list",
        params={"page": 1, "page_size": 20, "business_type": "DOC_SEARCH"},
        headers=_auth_headers(token),
    )
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["total"] == 1
    assert list_body["items"][0]["task_id"] == "task-1"
    assert list_body["items"][0]["task_status"] == "completed"

    detail_response = client.get("/chat/api/admin/logs/1", headers=_auth_headers(token))
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["final_response_type"] == "documents"
    assert detail_body["runs"][0]["request_id"] == "req-1"
    assert detail_body["runs"][0]["events"][0]["event_type"] == "request_received"
    assert detail_body["runs"][0]["model_provider"] == "openai"
    assert detail_body["runs"][0]["model_name"] == "gpt-4.1-mini"
    assert detail_body["runs"][0]["llm_call_count"] == 2
    assert detail_body["runs"][0]["llm_elapsed_ms"] == 210
    assert detail_body["runs"][0]["input_token_count"] == 1500
    assert detail_body["runs"][0]["estimated_cost_usd"] == 0.0012

    stats_response = client.get("/chat/api/admin/logs/stats/summary", headers=_auth_headers(token))
    assert stats_response.status_code == 200
    assert stats_response.json()["total"] == 1
    assert stats_response.json()["completed_count"] == 1

    export_response = client.get("/chat/api/admin/logs/export", headers=_auth_headers(token))
    assert export_response.status_code == 200
    assert "text/csv" in export_response.headers["content-type"]
    assert "task-1" in export_response.text


def test_admin_dashboard_summary(admin_client):
    client, token, _ = admin_client

    response = client.get("/chat/api/admin/dashboard/summary", headers=_auth_headers(token))
    assert response.status_code == 200
    body = response.json()

    assert body["dimensions"]["facet_count"] == 2
    assert body["dimensions"]["cache_loaded"] is True
    assert body["logs"]["total_count"] == 1
    assert body["logs"]["last_7d_count"] == 1
    assert body["logs"]["top_businesses"][0]["business_type"] == "DOC_SEARCH"
    assert body["logs"]["status_distribution"][0]["task_status"] == "completed"
    assert body["feedback"]["total_count"] == 1
    assert body["feedback"]["avg_rating_30d"] == 8.0


def test_admin_feedback_endpoints(admin_client):
    client, token, _ = admin_client

    list_response = client.get("/chat/api/admin/feedback/list", headers=_auth_headers(token))
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["total"] == 1
    assert list_body["items"][0]["task_log"]["task_id"] == "task-1"
    assert list_body["items"][0]["task_log"]["final_response_type"] == "documents"
    assert list_body["items"][0]["run_log"]["request_id"] == "req-1"
    assert list_body["items"][0]["chat_log"]["response_type"] == "documents"

    detail_response = client.get("/chat/api/admin/feedback/1", headers=_auth_headers(token))
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["task_log"]["task_id"] == "task-1"
    assert detail_body["task_log"]["runs"][0]["request_id"] == "req-1"
    assert detail_body["run_log"]["request_id"] == "req-1"
    assert detail_body["run_log"]["model_name"] == "gpt-4.1-mini"
    assert detail_body["chat_log"]["request_id"] == "req-1"

    stats_response = client.get("/chat/api/admin/feedback/stats", headers=_auth_headers(token))
    assert stats_response.status_code == 200
    stats_body = stats_response.json()
    assert stats_body["total_count"] == 1
    assert stats_body["top_tags"][0]["tag"] in {"结果准确", "返回快"}


def test_reconcile_system_configs_creates_deletes_and_updates_metadata():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    session.add_all(
        [
            SystemConfig(
                config_key="intent_provider",
                config_value="ollama",
                config_type="string",
                category="intent",
                description="旧配置",
                is_sensitive=False,
                updated_by="seed",
            ),
            SystemConfig(
                config_key="agent_model",
                config_value="google-gla:gemini-3.1-flash-lite-preview",
                config_type="string",
                category="legacy",
                description="旧描述",
                is_sensitive=True,
                updated_by="seed",
            ),
        ]
    )
    session.commit()

    result = reconcile_system_configs(session)

    remaining = {
        item.config_key: item
        for item in session.query(SystemConfig).all()
    }

    assert "intent_provider" not in remaining
    assert "agent_model" in remaining
    assert "agent_system_prompt" in remaining
    assert "llm_clarify_enabled" in remaining
    assert "llm_clarify_timeout" in remaining
    assert result["deleted_count"] == 1
    assert result["created_count"] >= 1
    assert "agent_model" in result["updated_meta"]
    assert "agent_model" in result["updated_values"]
    assert remaining["agent_model"].category == "llm"
    assert remaining["agent_model"].description == "Agent Loop 主模型，修改后下一次请求立即生效"
    assert remaining["agent_model"].is_sensitive is False
    assert remaining["agent_model"].config_value == "openrouter:google/gemini-3.1-flash-lite-preview"

    session.close()
