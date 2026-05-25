from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.judges.page import judge_page
from doc_search_bench.types import PredictedDocument, TargetDocumentTruth, TaskCase, build_case_run_result


def _build_task(
    *,
    accepted_pages: list[int] | None = None,
    accepted_page_ranges: list[tuple[int, int]] | None = None,
) -> TaskCase:
    return TaskCase(
        case_id="page_multi_target_case_001",
        split="dev",
        layer="component",
        suite_id="suite_page_multi_target",
        input_modality="text",
        question_text="find doc page",
        question_images=[],
        vehicle_info=None,
        preprocess_strategy="none",
        benchmark_track="chat_completions",
        request_context={},
        accepted_titles=["Doc A", "Doc B"],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="return page",
        initial_user_message="find doc page",
        top_k=5,
        page_goal_mode="shadow",
        accepted_pages=list(accepted_pages or []),
        accepted_page_ranges=list(accepted_page_ranges or []),
    )


def _build_result(task: TaskCase, *, predicted_pages: list[int], predicted_titles: list[str]):
    result = build_case_run_result(task, "run-page-multi-target")
    result.prediction.predicted_pages = list(predicted_pages)
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=index + 1,
            doc_title=title,
            doc_path=f"/docs/{index + 1}.pdf",
        )
        for index, title in enumerate(predicted_titles)
    ]
    return result


def test_judge_page_prefers_matched_target_truth_over_case_level_truth():
    task = _build_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(file_id="doc-a", title="Doc A", doc_path="/docs/a.pdf", accepted_pages=[3]),
            TargetDocumentTruth(file_id="doc-b", title="Doc B", doc_path="/docs/b.pdf", accepted_pages=[8]),
        ],
    )
    result = _build_result(task, predicted_pages=[8], predicted_titles=["Doc B"])
    setattr(result.metrics, "matched_targets", ["Doc B"])

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["truth_source"] == "matched_target_docs"
    assert report["page_hit_at_1"] is True
    assert report["page_hit_at_k"] is True
    assert report["exact_page_hit"] is True
    assert report["min_page_distance"] == 0


def test_judge_page_prefers_single_target_truth_over_case_level_truth():
    task = _build_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(file_id="doc-a", title="Doc A", doc_path="/docs/a.pdf", accepted_pages=[3]),
        ],
    )
    result = _build_result(task, predicted_pages=[3], predicted_titles=["Unrelated Doc"])

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["truth_source"] == "single_target_fallback"
    assert report["page_hit_at_1"] is True
    assert report["page_hit_at_k"] is True
    assert report["exact_page_hit"] is True
    assert report["min_page_distance"] == 0


def test_judge_page_can_infer_target_truth_from_predicted_documents():
    task = _build_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(file_id="doc-a", title="Doc A", doc_path="/docs/a.pdf", accepted_pages=[3]),
            TargetDocumentTruth(file_id="doc-b", title="Doc B", doc_path="/docs/b.pdf", accepted_pages=[8]),
        ],
    )
    result = _build_result(task, predicted_pages=[8], predicted_titles=["Doc B"])

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["truth_source"] == "matched_target_docs"
    assert report["page_hit_at_1"] is True
    assert report["page_hit_at_k"] is True
    assert report["exact_page_hit"] is True


def test_judge_page_uses_single_target_fallback_when_case_pages_are_missing():
    task = _build_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(file_id="doc-a", title="Doc A", doc_path="/docs/a.pdf", accepted_pages=[7]),
        ],
    )
    result = _build_result(task, predicted_pages=[7], predicted_titles=["Unrelated Doc"])

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["truth_source"] == "single_target_fallback"
    assert report["page_hit_at_1"] is True
    assert report["page_hit_at_k"] is True
    assert report["exact_page_hit"] is True


def test_judge_page_returns_unresolved_when_multi_target_truth_cannot_be_located():
    task = _build_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(file_id="doc-a", title="Doc A", doc_path="/docs/a.pdf", accepted_pages=[3]),
            TargetDocumentTruth(file_id="doc-b", title="Doc B", doc_path="/docs/b.pdf", accepted_pages=[8]),
        ],
    )
    result = _build_result(task, predicted_pages=[5], predicted_titles=["Unrelated Doc"])

    report = judge_page(task, result)

    assert report["eligible"] is False
    assert report["truth_source"] == "unresolved"
    assert report["page_hit_at_1"] is None
    assert report["page_hit_at_k"] is None
    assert report["exact_page_hit"] is None


def test_judge_page_uses_single_target_range_truth_without_case_level_fallback():
    task = _build_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(
                file_id="doc-legacy",
                title="Legacy Doc",
                doc_path="/docs/legacy.pdf",
                accepted_page_ranges=[(10, 12)],
            )
        ],
    )
    result = _build_result(task, predicted_pages=[11], predicted_titles=["Legacy Doc"])

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["truth_source"] == "matched_target_docs"
    assert report["page_hit_at_1"] is True
    assert report["page_hit_at_k"] is True
    assert report["exact_page_hit"] is False
    assert report["page_range_overlap_hit"] is True
    assert report["min_page_distance"] == 0
