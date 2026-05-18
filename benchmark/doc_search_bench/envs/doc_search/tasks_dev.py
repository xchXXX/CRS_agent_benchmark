from __future__ import annotations

from pathlib import Path

from ...types import TaskSuite, merge_suite_from_paths


DATA_DIR = Path(__file__).resolve().parent / "data" / "dev"


TASK_SUITES: list[TaskSuite] = [
    merge_suite_from_paths(
        split="dev",
        fixture_path=DATA_DIR / "real_text_single_turn.fixture.json",
        gold_path=DATA_DIR / "real_text_single_turn.gold.json",
        legacy_source_split="component",
    ),
    merge_suite_from_paths(
        split="dev",
        fixture_path=DATA_DIR / "real_image_augmented_single_turn.fixture.json",
        gold_path=DATA_DIR / "real_image_augmented_single_turn.gold.json",
        legacy_source_split="component",
    ),
]

TASKS = [case for suite in TASK_SUITES for case in suite.cases]
