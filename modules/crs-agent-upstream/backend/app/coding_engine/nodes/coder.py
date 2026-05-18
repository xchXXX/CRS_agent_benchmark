"""Coding and patch application nodes."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.coding_engine.model_router import invoke_coding_model
from app.coding_engine.scoping import coder_view
from app.coding_engine.state import CodingEngineState


DIFF_BLOCK_PATTERN = re.compile(r"```(?:diff|patch)?\s*(.*?)```", re.DOTALL)


def _extract_patch(value: str) -> str:
    match = DIFF_BLOCK_PATTERN.search(value)
    if match:
        return match.group(1).strip()
    stripped = value.strip()
    if stripped.startswith("diff --git") or stripped.startswith("--- "):
        return stripped
    return ""


def coder_node(state: CodingEngineState) -> CodingEngineState:
    view = coder_view(state)
    prompt = (
        "Use only this scoped public state. Do not assume hidden tests or private answers.\n"
        f"{view}\n\n"
        "Return a short rationale followed by a unified diff patch in a fenced ```diff block. "
        "Only modify files required by the task."
    )
    proposed = invoke_coding_model(
        "You are the coding worker in a harness-driven coding engine.",
        prompt,
        max_tokens=4096,
    )
    if not proposed:
        proposed = (
            "No model-generated patch is available. Provide OPENROUTER_API_KEY and rerun, "
            "or add operator guidance and resume."
        )

    return {
        "proposed_patch": proposed,
        "status": "coding",
        "phase": "code",
        "events": [
            {
                "phase": "code",
                "message": "Patch proposal generated.",
                "detail": {"has_diff": bool(_extract_patch(proposed))},
            }
        ],
    }


def apply_patch_node(state: CodingEngineState) -> CodingEngineState:
    if not state.get("sandbox_enabled") and not state.get("allow_unsandboxed_apply"):
        return {
            "applied_patch": False,
            "status": "needs_human",
            "phase": "apply_patch",
            "events": [
                {
                    "phase": "apply_patch",
                    "message": "Automatic patch application refused outside sandbox.",
                    "detail": {
                        "sandbox_enabled": bool(state.get("sandbox_enabled", False)),
                        "allow_unsandboxed_apply": bool(state.get("allow_unsandboxed_apply", False)),
                    },
                }
            ],
        }

    patch = _extract_patch(state.get("proposed_patch", ""))
    if not patch:
        return {
            "applied_patch": False,
            "status": "needs_human",
            "phase": "apply_patch",
            "events": [
                {
                    "phase": "apply_patch",
                    "message": "No unified diff was found in the patch proposal.",
                    "detail": {},
                }
            ],
        }

    workspace = Path(state["workspace_path"])
    check = subprocess.run(
        ["git", "apply", "--check", "-"],
        input=patch,
        text=True,
        cwd=str(workspace),
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        return {
            "applied_patch": False,
            "status": "needs_human",
            "phase": "apply_patch",
            "events": [
                {
                    "phase": "apply_patch",
                    "message": "Patch failed git apply --check.",
                    "detail": {"stderr": check.stderr[-4000:]},
                }
            ],
        }

    apply = subprocess.run(
        ["git", "apply", "-"],
        input=patch,
        text=True,
        cwd=str(workspace),
        capture_output=True,
        check=False,
    )
    return {
        "applied_patch": apply.returncode == 0,
        "status": "applying_patch" if apply.returncode == 0 else "needs_human",
        "phase": "apply_patch",
        "events": [
            {
                "phase": "apply_patch",
                "message": "Patch applied." if apply.returncode == 0 else "Patch application failed.",
                "detail": {"stderr": apply.stderr[-4000:]},
            }
        ],
    }


def route_after_coder(state: CodingEngineState) -> str:
    if state.get("auto_apply_patch"):
        return "apply_patch"
    return "human_gate"


def route_after_apply(state: CodingEngineState) -> str:
    if state.get("applied_patch"):
        return "run_harness"
    return "finish"

