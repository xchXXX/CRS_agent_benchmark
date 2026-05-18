import asyncio
from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest

from app.agent.domain.repair_knowledge.rendering import (
    RepairAnswerDepth,
    RepairAnswerFrame,
    RepairRenderPlan,
    build_repair_render_context,
    build_repair_render_fallback_content,
    default_repair_render_plan,
    review_repair_rendered_answer,
    validate_repair_render_plan,
)
from app.agent.memory.deferred_store import DeferredStateStore
from app.agent.memory.mem0_store import Mem0Store
from app.agent.memory.message_history_store import MessageHistoryStore
from app.agent.observability.tracer import LoopTracer
from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.runtime.service import AgentLoopService, RepairAnswerGateReadyState, RepairRenderRuntimeState
from app.agent.tools.registry import build_default_tool_registry


def build_test_deps(tmp_path) -> AgentRuntimeDeps:
    return AgentRuntimeDeps(
        tool_registry=build_default_tool_registry(),
        message_history_store=MessageHistoryStore(base_dir=str(tmp_path / "history")),
        deferred_state_store=DeferredStateStore(base_dir=str(tmp_path / "deferred")),
        mem0_store=Mem0Store(),
        tracer=LoopTracer(),
    )


def test_default_repair_render_plan_matches_real_query_families():
    assert default_repair_render_plan(
        build_repair_render_context(query="雷沃挖机检测口在那里")
    ).frame == RepairAnswerFrame.LOCATION_IDENTIFICATION
    assert default_repair_render_plan(
        build_repair_render_context(query="尿素泵工作原理")
    ).frame == RepairAnswerFrame.PRINCIPLE_EXPLANATION
    assert default_repair_render_plan(
        build_repair_render_context(query="CAN 总线电阻正常是多少")
    ).frame == RepairAnswerFrame.SPEC_ANSWER
    assert default_repair_render_plan(
        build_repair_render_context(query="J1939 通讯故障怎么排查")
    ).frame == RepairAnswerFrame.SYMPTOM_DIAGNOSIS
    assert default_repair_render_plan(
        build_repair_render_context(query="传感器 5V 供电短路怎么查")
    ).frame == RepairAnswerFrame.SYMPTOM_DIAGNOSIS
    assert default_repair_render_plan(
        build_repair_render_context(query="P20EE 怎么处理")
    ).frame == RepairAnswerFrame.DTC_DIAGNOSIS


def test_validate_rejects_electrical_fault_query_rendered_as_spec_answer():
    context = build_repair_render_context(
        query="传感器 5V 供电短路怎么查",
        summary_text="相关系统/模块：车身控制器BCM；近期维修/检查史：无近期检修（首次排查）",
    )
    plan = RepairRenderPlan(
        frame=RepairAnswerFrame.SPEC_ANSWER,
        response_goal="直接给出参数或标准值，并说明适用前提和现场核对方法。",
        answer_depth=RepairAnswerDepth.DIRECT,
        required_elements=["直接结论", "适用前提", "现场核对"],
        need_thresholds=True,
        focus_points=["先给参数结论，再说明适用前提"],
    )

    valid, reasons = validate_repair_render_plan(plan, context=context)

    assert context.has_symptom_signal is True
    assert valid is False
    assert "symptom_query_misframed_as_spec" in reasons


def test_review_rejects_location_query_rendered_as_diagnosis():
    context = build_repair_render_context(query="雷沃挖机检测口在那里")
    plan = RepairRenderPlan(
        frame=RepairAnswerFrame.LOCATION_IDENTIFICATION,
        response_goal="帮助用户定位接口位置并说明确认方法。",
        answer_depth=RepairAnswerDepth.STANDARD,
        required_elements=["通常位置", "现场确认方法"],
        focus_points=["不要写成诊断模板"],
    )

    review = review_repair_rendered_answer(
        content=(
            "### 故障定义\n老哥，当前属于通用故障诊断场景。\n\n"
            "### 当前更像哪一型\n更像基础条件异常。\n\n"
            "### 分步检查\n先查电源。"
        ),
        plan=plan,
        context=context,
    )

    assert review.accepted is False
    assert "location_rendered_as_diagnosis" in review.reasons


def test_location_frame_fallback_uses_location_sections():
    context = build_repair_render_context(
        query="雷沃挖机检测口在那里",
        summary_text="6 吨级",
    )
    plan = RepairRenderPlan(
        frame=RepairAnswerFrame.LOCATION_IDENTIFICATION,
        response_goal="帮助用户定位诊断口位置。",
        answer_depth=RepairAnswerDepth.STANDARD,
        required_elements=["通常位置", "现场确认方法"],
        focus_points=["说明通常位置和确认方法"],
    )

    content = build_repair_render_fallback_content(plan=plan, context=context)

    assert content.startswith("### 先判断是哪一类接口或部件\n老哥，")
    assert "### 通常位置" in content
    assert "### 现场确认方法" in content
    assert "### 易混点" in content


def test_service_prepare_render_state_uses_planner_frame(tmp_path):
    service = AgentLoopService(deps=build_test_deps(tmp_path))
    active_deps = service._prepare_request_runtime_deps(
        runtime_deps=service._deps,
        request=SimpleNamespace(message="雷沃挖机检测口在那里", ask_user_answer=None, mode="general_chat", session_id=None),
        session_id="repair-render-state",
    )
    ready_state = RepairAnswerGateReadyState(
        message_history=[ModelRequest.user_text_prompt("雷沃挖机检测口在那里")],
        query="雷沃挖机检测口在那里",
        run_messages=[],
    )

    class DummyPlannerAgent:
        async def run(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                output={
                    "frame": "location_identification",
                    "response_goal": "帮助用户定位诊断接口位置，并说明现场确认方法。",
                    "confidence": "high",
                    "answer_depth": "standard",
                    "required_elements": ["通常位置", "现场确认方法"],
                    "optional_elements": ["易混点"],
                    "min_steps": 2,
                    "need_thresholds": False,
                    "need_branching": False,
                    "need_recheck": False,
                    "focus_points": ["不要写成诊断模板", "直接回答位置和确认方式"],
                    "keep_mechanic_tone": True,
                    "forbid_followup_text": True,
                }
            )

    runtime_state = asyncio.run(
        service._prepare_repair_render_runtime_state(
            request=SimpleNamespace(message="雷沃挖机检测口在那里", ask_user_answer=None),
            active_deps=active_deps,
            ready_state=ready_state,
            repair_render_planner_agent=DummyPlannerAgent(),
        )
    )

    assert runtime_state.plan.frame == RepairAnswerFrame.LOCATION_IDENTIFICATION
    assert "### 通常位置" in runtime_state.user_prompt
    assert "不要写成诊断排故模板" in runtime_state.user_prompt


def test_service_finalize_repair_rendered_content_marks_review_failure_when_frame_is_wrong(tmp_path):
    service = AgentLoopService(deps=build_test_deps(tmp_path))
    render_state = RepairRenderRuntimeState(
        message_history=[],
        user_prompt="unused",
        run_messages=[],
        plan=RepairRenderPlan(
            frame=RepairAnswerFrame.LOCATION_IDENTIFICATION,
            response_goal="帮助用户定位诊断接口位置。",
            answer_depth=RepairAnswerDepth.STANDARD,
            required_elements=["通常位置", "现场确认方法"],
            focus_points=["直接回答位置"],
        ),
        context=build_repair_render_context(query="雷沃挖机检测口在那里", summary_text="6 吨级"),
    )

    content, metadata = service._finalize_repair_rendered_content(
        content=(
            "### 故障定义\n老哥，当前更像基础条件异常。\n\n"
            "### 当前更像哪一型\n更像通用排故。\n\n"
            "### 分步检查\n先查供电。"
        ),
        extra_metadata={},
        render_state=render_state,
    )

    assert metadata["repair_render_review_failed"] is True
    assert metadata["repair_render_frame"] == "location_identification"
    assert isinstance(content, str)
    assert "### 故障定义" in content
