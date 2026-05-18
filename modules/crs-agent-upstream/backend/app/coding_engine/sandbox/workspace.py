"""Workspace preparation for coding-engine runs."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.coding_engine.config import CODING_RUNS_DIR, PROJECT_ROOT


IGNORE_PATTERNS = (
    ".git",
    ".venv",
    ".langgraph_api",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "logs",
    ".DS_Store",
)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def prepare_workspace(
    *,
    run_id: str,
    workspace_path: str | None,
    sandbox_enabled: bool,
) -> Path:
    if workspace_path:
        return Path(workspace_path).expanduser().resolve()

    if not sandbox_enabled:
        return PROJECT_ROOT

    run_root = CODING_RUNS_DIR / run_id
    workspace = run_root / "workspace"
    if workspace.exists():
        return workspace

    run_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        PROJECT_ROOT,
        workspace,
        ignore=shutil.ignore_patterns(*IGNORE_PATTERNS),
    )
    return workspace

