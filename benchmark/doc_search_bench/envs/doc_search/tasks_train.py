from __future__ import annotations

from pathlib import Path

from ...types import TaskSuite, merge_suite_from_paths


DATA_DIR = Path(__file__).resolve().parent / "data" / "train"


TASK_SUITES: list[TaskSuite] = [
    merge_suite_from_paths(
        split="train",
        fixture_path=DATA_DIR / "real_world_wecom_train.fixture.json",
        gold_path=DATA_DIR / "real_world_wecom_train.gold.json",
        legacy_source_split="atomic",
    ),
]

TASKS = [case for suite in TASK_SUITES for case in suite.cases]
