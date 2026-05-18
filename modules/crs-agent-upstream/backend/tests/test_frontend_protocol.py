from app.agent.adapters.frontend_protocol import FrontendProtocolAdapter
from app.agent.models.ask_user import AskUserInputType, AskUserOption, AskUserQuestion
from app.agent.models.events import AgentEventType, AgentRuntimeEvent


def test_protocol_adapter_maps_text_delta_to_chunk():
    adapter = FrontendProtocolAdapter()
    event = AgentRuntimeEvent(
        type=AgentEventType.TEXT_DELTA,
        session_id="sess_1",
        content="hello",
    )

    assert adapter.to_event(event) == {
        "type": "chunk",
        "session_id": "sess_1",
        "content": "hello",
    }


def test_protocol_adapter_maps_ask_user_event():
    adapter = FrontendProtocolAdapter()
    question = AskUserQuestion(
        tool_call_id="call_1",
        question="请选择车型",
        input_type=AskUserInputType.SINGLE_SELECT,
        options=[AskUserOption(key="j6", label="解放 J6", description="重卡平台")],
        allow_free_input=True,
        input_hint="也可以直接输入车型",
    )
    event = AgentRuntimeEvent(
        type=AgentEventType.ASK_USER,
        session_id="sess_2",
        ask_user=question,
    )

    assert adapter.to_event(event) == {
        "type": "ask_user",
        "session_id": "sess_2",
        "tool_call_id": "call_1",
        "question": "请选择车型",
        "input_type": "single_select",
        "options": [
            {
                "key": "j6",
                "label": "解放 J6",
                "description": "重卡平台",
                "selection_payload": {"filters": {}, "file_ids": []},
            }
        ],
        "allow_free_input": True,
        "input_hint": "也可以直接输入车型",
        "unit": None,
        "reference_range": None,
        "context": {},
    }
