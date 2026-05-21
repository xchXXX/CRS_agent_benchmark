from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.judges.file import judge_file
from doc_search_bench.types import (
    PredictedDocument,
    TargetDocumentTruth,
    TaskCase,
    build_case_run_result,
)


def _build_task(*, accepted_titles: list[str]) -> TaskCase:
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
        accepted_titles=accepted_titles,
        preferred_title=None,
        user_id="benchmark_user",
        instruction="请返回正确资料。",
        initial_user_message="帮我找一下相关资料",
        top_k=5,
    )


def _attach_multi_target(task: TaskCase, *, mode: str, titles: list[str]) -> None:
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(
                file_id=f"doc-{index + 1}",
                title=title,
                doc_path=f"/docs/doc-{index + 1}.pdf",
            )
            for index, title in enumerate(titles)
        ],
    )
    object.__setattr__(task, "target_match_mode", mode)


def _build_result(task: TaskCase, predicted_titles: list[str]):
    result = build_case_run_result(task, "run-multi-target")
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=index + 1,
            doc_title=title,
            doc_path=f"/docs/predicted-{index + 1}.pdf",
        )
        for index, title in enumerate(predicted_titles)
    ]
    return result


def test_judge_file_any_of_reports_partial_multi_target_coverage():
    task = _build_task(accepted_titles=["资料A", "资料B"])
    _attach_multi_target(task, mode="any_of", titles=["资料A", "资料B"])
    result = _build_result(task, ["资料B", "其他资料"])

    report = judge_file(task, result)

    assert report["pass"] is True
    assert report["target_match_mode"] == "any_of"
    assert report["matched_targets"] == ["资料B"]
    assert report["missed_targets"] == ["资料A"]
    assert report["matched_target_count"] == 1
    assert report["target_doc_count"] == 2
    assert report["target_coverage_rate"] == 0.5
    assert report["all_targets_hit"] is False


def test_judge_file_all_of_requires_full_target_coverage():
    task = _build_task(accepted_titles=["资料A", "资料B"])
    _attach_multi_target(task, mode="all_of", titles=["资料A", "资料B"])
    result = _build_result(task, ["资料B", "其他资料"])

    report = judge_file(task, result)

    assert report["pass"] is False
    assert report["recall_hit"] is False
    assert report["matched_targets"] == ["资料B"]
    assert report["missed_targets"] == ["资料A"]
    assert report["target_coverage_rate"] == 0.5
    assert "TARGET_SET_INCOMPLETE" in report["blocking_failures"]
