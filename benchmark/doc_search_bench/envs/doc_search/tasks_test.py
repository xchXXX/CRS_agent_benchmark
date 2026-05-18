from __future__ import annotations

from pathlib import Path

from ...types import TaskSuite, merge_suite_from_paths


DATA_DIR = Path(__file__).resolve().parent / "data" / "test"


TASK_SUITES: list[TaskSuite] = [
    merge_suite_from_paths(
        split="test",
        fixture_path=DATA_DIR / "real_acceptance_visible.fixture.json",
        gold_path=DATA_DIR / "real_acceptance_visible.gold.json",
        legacy_source_split="e2e",
    ),
    merge_suite_from_paths(
        split="test",
        fixture_path=DATA_DIR / "real_acceptance_holdout.fixture.json",
        gold_path=DATA_DIR / "real_acceptance_holdout.gold.json",
        legacy_source_split="blind",
    ),
]

TASKS = [case for suite in TASK_SUITES for case in suite.cases]
