from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.observability.task_log_service import AgentTaskLogService
from app.agent.observability.tracer import LoopTracer
from app.legacy.models.database import Base, ChatRunEventLog, ChatRunLog, ChatTaskLog
from app.schemas.chat import ChatRequest, ChatResponse


def test_task_log_service_persists_task_run_and_events():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    tracer = LoopTracer()
    tracer.trace(
        event_type="agent_loop_guard_after_tool_call",
        session_id="sess-1",
        payload={"tool_name": "search_documents", "tool_category": "external", "info_gain": "high"},
    )
    tracer.trace(
        event_type="agent_loop_ask_user",
        session_id="sess-1",
        payload={"tool_call_id": "tool-1", "question": "请补充车型"},
    )

    request = ChatRequest(
        message="冷车难启动怎么办",
        session_id="sess-1",
        client_type="web",
    )
    response = ChatResponse(
        type="ask_user",
        content={
            "tool_call_id": "tool-1",
            "question": "请补充车型",
        },
        session_id="sess-1",
        request_id="req-1",
        business="GENERAL_CHAT",
        metadata={
            "runtime": "pydantic_ai",
            "llm": {
                "model_name": "gpt-4o-mini",
                "provider_name": "openai",
                "call_count": 2,
                "aggregate_llm_elapsed_ms": 123,
                "aggregate_first_response_ms": 45,
                "aggregate_estimated_cost_usd": 0.00021,
                "aggregate_usage": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "total_tokens": 1100,
                    "reasoning_tokens": 10,
                    "request_count": 1,
                    "tool_call_count": 1,
                    "details": {},
                },
                "calls": [
                    {"phase": "intent_router", "model_name": "gpt-4o-mini"},
                    {"phase": "agent_loop", "model_name": "gpt-4o-mini"},
                ],
            },
        },
    )

    AgentTaskLogService(SessionLocal).persist_interaction(
        request=request,
        response=response,
        user_id=1001,
        trace_entries=tracer.entries(),
        elapsed_ms=860,
        transport="http",
    )

    session = SessionLocal()
    try:
        task = session.query(ChatTaskLog).filter(ChatTaskLog.task_id.isnot(None)).first()
        assert task is not None
        assert task.session_id == "sess-1"
        assert task.task_status == "waiting_user"
        assert task.ask_user_triggered is True
        assert task.tool_call_count == 1
        assert task.external_tool_call_count == 1

        run = session.query(ChatRunLog).filter(ChatRunLog.task_id == task.task_id).first()
        assert run is not None
        assert run.request_id == "req-1"
        assert run.run_status == "waiting_user"
        assert run.transport == "http"
        assert run.model_provider == "openai"
        assert run.model_name == "gpt-4o-mini"
        assert run.llm_call_count == 2
        assert run.llm_elapsed_ms == 123
        assert run.llm_first_response_ms == 45
        assert run.llm_request_count == 1
        assert run.input_token_count == 1000
        assert run.output_token_count == 100
        assert run.total_token_count == 1100
        assert run.reasoning_token_count == 10
        assert float(run.estimated_cost_usd) == 0.00021

        events = (
            session.query(ChatRunEventLog)
            .filter(ChatRunEventLog.run_id == run.run_id)
            .order_by(ChatRunEventLog.sequence_no.asc())
            .all()
        )
        assert len(events) >= 3
        assert events[0].event_type == "request_received"
        assert events[0].detail == "冷车难启动怎么办"
        assert any(event.event_type == "agent_loop_ask_user" for event in events)
        assert events[-1].event_type == "response_emitted"
    finally:
        session.close()
