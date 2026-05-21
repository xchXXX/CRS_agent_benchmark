from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench import run as benchmark_run
from doc_search_bench.types import TaskCase, build_case_run_result


def _build_task() -> TaskCase:
    return TaskCase(
        case_id="multi_target_case_001",
        split="dev",
        layer="component",
        suite_id="suite_multi_target",
        input_modality="text",
        question_text="帮我找一下相关资料",
        question_images=[],
        vehicle_info=None,
        preprocess_strategy="none",
        benchmark_track="chat_completions",
        request_context={},
        accepted_titles=["资料A", "资料B"],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="请返回正确资料。",
        initial_user_message="帮我找一下相关资料",
    )


def _build_attempt(
    *,
    attempt_index: int,
    recall_hit: bool,
    blocking_failures: list[str],
    matched_target_count: int,
    target_coverage_rate: float,
    all_targets_hit: bool,
):
    task = _build_task()
    result = build_case_run_result(task, "run-multi-target", attempt_index=attempt_index)
    result.metrics.recall_hit = recall_hit
    result.validation.blocking_failures = list(blocking_failures)
    result.analysis.final_hit = recall_hit and not blocking_failures
    result.response.final_status = "success_documents" if not blocking_failures else "failed_documents"
    setattr(result.task_metadata, "target_match_mode", "all_of")
    setattr(result.task_metadata, "target_doc_count", 2)
    setattr(result.metrics, "matched_target_count", matched_target_count)
    setattr(result.metrics, "target_coverage_rate", target_coverage_rate)
    setattr(result.metrics, "all_targets_hit", all_targets_hit)
    return result


def test_build_case_rollups_aggregates_multi_target_attempt_coverage():
    attempt_partial = _build_attempt(
        attempt_index=1,
        recall_hit=False,
        blocking_failures=["TARGET_SET_INCOMPLETE"],
        matched_target_count=1,
        target_coverage_rate=0.5,
        all_targets_hit=False,
    )
    attempt_full = _build_attempt(
        attempt_index=2,
        recall_hit=True,
        blocking_failures=[],
        matched_target_count=2,
        target_coverage_rate=1.0,
        all_targets_hit=True,
    )

    rollups = benchmark_run.build_case_rollups([attempt_partial, attempt_full])

    assert len(rollups) == 1
    rollup = rollups[0]
    assert rollup["target_match_mode"] == "all_of"
    assert rollup["target_doc_count"] == 2
    assert rollup["partial_target_hit_attempt_count"] == 1
    assert rollup["full_target_hit_attempt_count"] == 1
    assert rollup["max_target_coverage_rate"] == 1.0
    assert rollup["min_target_coverage_rate"] == 0.5
