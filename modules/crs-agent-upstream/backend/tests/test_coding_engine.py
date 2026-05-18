from __future__ import annotations

import json
from pathlib import Path

from app.coding_engine.graph import graph
from app.coding_engine.harness.shell import run_shell_harness
from app.coding_engine.scoping import coder_view


def test_coder_view_hides_raw_logs_and_private_fields() -> None:
    scoped = coder_view(
        {
            "task": "fix parser",
            "public_logs": "public failure",
            "raw_logs": "private raw log",
            "failed_tests": ["test_parser"],
        }
    )

    assert scoped["task"] == "fix parser"
    assert scoped["public_logs"] == "public failure"
    assert "raw_logs" not in scoped


def test_shell_harness_reports_pass(tmp_path: Path) -> None:
    result = run_shell_harness(
        "python -c \"print('ok')\"",
        cwd=tmp_path,
        timeout_seconds=5,
        sanitize_env=True,
    )

    assert result.passed is True
    assert result.exit_code == 0
    assert "ok" in result.public_logs


def test_coding_engine_graph_smoke() -> None:
    result = graph.invoke(
        {
            "task": "smoke test",
            "harness_command": "python -c \"print('harness ok')\"",
            "max_iterations": 1,
        }
    )

    assert result["status"] == "passed"
    assert result["passed"] is True


def test_coding_engine_persists_run_artifacts(tmp_path: Path) -> None:
    run_root = tmp_path / "run-artifacts"
    result = graph.invoke(
        {
            "task": "persist smoke",
            "harness_command": "python -c \"print('persist ok')\"",
            "max_iterations": 1,
            "run_root": str(run_root),
        }
    )

    assert result["status"] == "passed"
    assert (run_root / "state.latest.json").exists()
    assert (run_root / "attempts.jsonl").exists()
    assert (run_root / "events.jsonl").exists()
    assert (run_root / "harness.iteration-001.log").exists()

    state_snapshot = json.loads((run_root / "state.latest.json").read_text(encoding="utf-8"))
    assert state_snapshot["status"] == "passed"
    assert state_snapshot["task"] == "persist smoke"
    assert "persisted_event_count" not in state_snapshot
