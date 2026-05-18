import pytest

from app.agent.context.guard import LoopGuard, LoopGuardExceededError


def test_loop_guard_blocks_repeated_same_args():
    guard = LoopGuard(
        max_tool_calls=5,
        max_same_tool_repeat=5,
        max_same_args_repeat=1,
    )

    guard.before_tool_call("query_parameters", {"query": "K46 是什么作用"})

    with pytest.raises(LoopGuardExceededError) as exc_info:
        guard.before_tool_call("query_parameters", {"query": "K46 是什么作用"})

    assert exc_info.value.error_code == "LOOP_GUARD_MAX_SAME_ARGS_REPEAT"
    assert exc_info.value.tool_name == "query_parameters"


def test_loop_guard_blocks_total_budget():
    guard = LoopGuard(
        max_tool_calls=2,
        max_same_tool_repeat=3,
        max_same_args_repeat=3,
    )

    guard.before_tool_call("lookup_repair_knowledge_titles", {"query": "冒黑烟"})
    guard.before_tool_call("get_repair_knowledge_context", {"entry_ids": ["1"]})

    with pytest.raises(LoopGuardExceededError) as exc_info:
        guard.before_tool_call("query_parameters", {"query": "K46 是什么作用"})

    assert exc_info.value.error_code == "LOOP_GUARD_MAX_TOOL_CALLS"
    assert exc_info.value.tool_name == "query_parameters"


def test_loop_guard_normalizes_args_before_repeat_check():
    guard = LoopGuard(
        max_tool_calls=4,
        max_same_tool_repeat=4,
        max_same_args_repeat=1,
    )

    guard.before_tool_call("query_parameters", {"query": " K46   是什么作用 ", "selection_payload": {"file_ids": ["2", "1"]}})

    with pytest.raises(LoopGuardExceededError) as exc_info:
        guard.before_tool_call("query_parameters", {"selection_payload": {"file_ids": ["1", "2"]}, "query": "K46 是什么作用"})

    assert exc_info.value.error_code == "LOOP_GUARD_MAX_SAME_ARGS_REPEAT"


def test_loop_guard_blocks_external_budget():
    guard = LoopGuard(
        max_tool_calls=4,
        max_external_tool_calls=1,
        max_same_tool_repeat=4,
        max_same_args_repeat=4,
    )

    guard.before_tool_call("lookup_ecu_candidates", {"fault_code": "P0101"}, is_external=True)

    with pytest.raises(LoopGuardExceededError) as exc_info:
        guard.before_tool_call("dtc_diagnosis", {"fault_code": "P0101", "ecu_model": "EDC17"}, is_external=True)

    assert exc_info.value.error_code == "LOOP_GUARD_MAX_EXTERNAL_TOOL_CALLS"


def test_loop_guard_blocks_ask_user_budget():
    guard = LoopGuard(
        max_tool_calls=4,
        max_ask_user_calls=1,
        max_same_tool_repeat=4,
        max_same_args_repeat=4,
    )

    guard.before_tool_call("ask_user_question", {"question": "请选择车型"}, is_user_interaction=True)

    with pytest.raises(LoopGuardExceededError) as exc_info:
        guard.before_tool_call("ask_user_question", {"question": "请选择 ECU"}, is_user_interaction=True)

    assert exc_info.value.error_code == "LOOP_GUARD_MAX_ASK_USER_CALLS"


def test_loop_guard_blocks_no_gain_streak():
    guard = LoopGuard(
        max_tool_calls=4,
        max_same_tool_repeat=4,
        max_same_args_repeat=4,
        max_no_gain_streak=1,
    )

    guard.before_tool_call("query_parameters", {"query": "A1"})
    assert guard.after_tool_call("query_parameters", {"status": "failed", "data": {"message": "unavailable"}}) == "none"

    guard.before_tool_call("query_parameters", {"query": "A2"})
    with pytest.raises(LoopGuardExceededError) as exc_info:
        guard.after_tool_call("query_parameters", {"status": "failed", "data": {"message": "unavailable"}})

    assert exc_info.value.error_code == "LOOP_GUARD_MAX_NO_GAIN_STREAK"
