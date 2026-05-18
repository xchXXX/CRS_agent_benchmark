from app.agent.context.models import (
    CaseContext,
    CaseContextArtifact,
    CaseContextAttemptedAction,
    CaseContextArtifactType,
    CaseContextCandidateAnswer,
    CaseContextPendingAction,
    CaseContextRemainingBudget,
)
from app.agent.context.prompt_builder import CaseContextPromptBuilder


def test_case_context_prompt_builder_returns_empty_for_blank_context():
    builder = CaseContextPromptBuilder()

    assert builder.build(CaseContext(session_id="blank")) == ""


def test_case_context_prompt_builder_respects_max_chars_and_keeps_wrappers():
    context = CaseContext(session_id="prompt_case")
    context.task_type = "PARAM_QUERY"
    context.slots.brand = "东风"
    context.slots.series = "天锦"
    context.slots.model = "KR220"
    context.missing_slots = ["ecu_model", "parameter_source_id"]
    context.no_gain_streak = 1
    context.remaining_budget = CaseContextRemainingBudget(
        tool_calls_left=4,
        external_calls_left=1,
        ask_user_calls_left=1,
    )
    context.candidate_answer = CaseContextCandidateAnswer(
        business="PARAM_QUERY",
        summary="参数命中：EDC17C53针脚电压(12V系统)。",
        source="query_parameters",
    )
    context.attempted_actions.append(
        CaseContextAttemptedAction(
            action="query_parameters",
            args_signature="abc123",
            result_summary="参数命中：EDC17C53针脚电压(12V系统)。",
            info_gain="medium",
            filled_slots=["ecu_model", "parameter_source_id"],
        )
    )
    context.pending_action = CaseContextPendingAction(
        scene="doc_search",
        tool_call_id="ask_user_1",
        business="DOC_SEARCH",
        question="请选择车型系列",
        options_summary=["天锦", "天龙", "多利卡"],
    )
    for idx in range(5):
        context.artifacts.append(
            CaseContextArtifact(
                artifact_id=f"artifact_{idx}",
                type=CaseContextArtifactType.DOC_SEARCH_RESULT,
                source_business="DOC_SEARCH",
                summary=f"资料搜索命中很多结果，第 {idx + 1} 条摘要 " + ("很长的说明 " * 12),
            )
        )

    prompt = CaseContextPromptBuilder(max_artifacts=5, max_chars=220).build(context)

    assert prompt.startswith("[CASE_CONTEXT]")
    assert prompt.endswith("[/CASE_CONTEXT]")
    assert len(prompt) <= 220
    assert "东风" in prompt
    assert "天锦" in prompt
    assert "缺失信息" in prompt or "候选结论" in prompt or "最近动作" in prompt
