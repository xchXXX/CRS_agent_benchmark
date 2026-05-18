"""LangGraph entrypoint for the dev-time coding engine."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.coding_engine.nodes.coder import (
    apply_patch_node,
    coder_node,
    route_after_apply,
    route_after_coder,
)
from app.coding_engine.nodes.harness import (
    judge_node,
    route_after_judge,
    run_harness_node,
)
from app.coding_engine.nodes.lifecycle import (
    bootstrap_node,
    human_gate_node,
    prepare_workspace_node,
)
from app.coding_engine.nodes.persistence import persist_run_node
from app.coding_engine.nodes.planner import planner_node, reflect_node
from app.coding_engine.state import CodingEngineState


builder = StateGraph(CodingEngineState)

builder.add_node("bootstrap", bootstrap_node)
builder.add_node("persist_bootstrap", persist_run_node)
builder.add_node("prepare_workspace", prepare_workspace_node)
builder.add_node("persist_workspace", persist_run_node)
builder.add_node("plan", planner_node)
builder.add_node("persist_plan", persist_run_node)
builder.add_node("run_harness", run_harness_node)
builder.add_node("persist_harness", persist_run_node)
builder.add_node("judge", judge_node)
builder.add_node("persist_judge", persist_run_node)
builder.add_node("reflect", reflect_node)
builder.add_node("persist_reflect", persist_run_node)
builder.add_node("code", coder_node)
builder.add_node("persist_code", persist_run_node)
builder.add_node("apply_patch", apply_patch_node)
builder.add_node("persist_apply_patch", persist_run_node)
builder.add_node("human_gate", human_gate_node)
builder.add_node("persist_human_gate", persist_run_node)

builder.add_edge(START, "bootstrap")
builder.add_edge("bootstrap", "persist_bootstrap")
builder.add_edge("persist_bootstrap", "prepare_workspace")
builder.add_edge("prepare_workspace", "persist_workspace")
builder.add_edge("persist_workspace", "plan")
builder.add_edge("plan", "persist_plan")
builder.add_edge("persist_plan", "run_harness")
builder.add_edge("run_harness", "persist_harness")
builder.add_edge("persist_harness", "judge")
builder.add_edge("judge", "persist_judge")
builder.add_conditional_edges(
    "persist_judge",
    route_after_judge,
    {
        "reflect": "reflect",
        "finish": END,
    },
)
builder.add_edge("reflect", "persist_reflect")
builder.add_edge("persist_reflect", "code")
builder.add_edge("code", "persist_code")
builder.add_conditional_edges(
    "persist_code",
    route_after_coder,
    {
        "apply_patch": "apply_patch",
        "human_gate": "human_gate",
    },
)
builder.add_edge("apply_patch", "persist_apply_patch")
builder.add_conditional_edges(
    "persist_apply_patch",
    route_after_apply,
    {
        "run_harness": "run_harness",
        "finish": END,
    },
)
builder.add_edge("human_gate", "persist_human_gate")
builder.add_edge("persist_human_gate", END)

graph = builder.compile()
