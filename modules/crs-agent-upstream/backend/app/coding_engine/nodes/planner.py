"""Planning and reflection nodes."""

from __future__ import annotations

from app.coding_engine.model_router import invoke_coding_model
from app.coding_engine.scoping import coder_view
from app.coding_engine.state import CodingEngineState


def planner_node(state: CodingEngineState) -> CodingEngineState:
    task = state.get("task", "").strip()
    requirements = state.get("requirements", "").strip()
    if not task and not requirements:
        plan = "No coding task was provided. Configure `task` before running a real benchmark."
    else:
        prompt = (
            "Task:\n"
            f"{task}\n\n"
            "Requirements:\n"
            f"{requirements}\n\n"
            "Produce a concise implementation plan. Do not write code yet."
        )
        plan = invoke_coding_model(
            "You are the planner for a harness-driven coding engine.",
            prompt,
            max_tokens=1024,
        ) or "Model unavailable. Start by running the harness and use failures to guide changes."

    return {
        "plan": plan,
        "status": "planning",
        "phase": "plan",
        "events": [{"phase": "plan", "message": "Plan generated.", "detail": {}}],
    }


def reflect_node(state: CodingEngineState) -> CodingEngineState:
    view = coder_view(state)
    prompt = (
        "You are reviewing a failed harness attempt. Use only this public view:\n"
        f"{view}\n\n"
        "Summarize the likely next fix in 5 bullet points or fewer."
    )
    reflection = invoke_coding_model(
        "You are the reflection node in a harness-driven coding loop.",
        prompt,
        max_tokens=1024,
    )
    if not reflection:
        reflection = (
            f"{state.get('harness_summary', 'Harness failed.')}\n"
            "No model reflection was produced; inspect public logs before changing files."
        )

    return {
        "reflection": reflection,
        "status": "reflecting",
        "phase": "reflect",
        "events": [{"phase": "reflect", "message": "Failure reflected.", "detail": {}}],
    }

