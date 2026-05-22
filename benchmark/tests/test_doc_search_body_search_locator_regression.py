from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.envs.doc_search.env import apply_locator_prediction, normalize_documents
from doc_search_bench.judges.coord import aggregate_coord_reports, judge_coord
from doc_search_bench.judges.locator import aggregate_locator_reports, judge_locator
from doc_search_bench.judges.page import judge_page
from doc_search_bench.run import aggregate_case_rollup_coord, build_case_rollups, build_score_report
from doc_search_bench.types import (
    AcceptedRegionGroup,
    PredictedDocument,
    RegionPageBoxes,
    TargetDocumentTruth,
    TaskCase,
    TaskSuite,
    build_case_run_result,
)


def _build_task(
    *,
    accepted_pages: list[int] | None = None,
    accepted_page_ranges: list[tuple[int, int]] | None = None,
) -> TaskCase:
    return TaskCase(
        case_id="locator_regression_case_001",
        split="dev",
        layer="component",
        suite_id="suite_locator_regression",
        input_modality="text",
        question_text="帮我定位文档内容",
        question_images=[],
        vehicle_info=None,
        preprocess_strategy="none",
        benchmark_track="chat_completions",
        request_context={},
        accepted_titles=["资料A"],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="返回定位页码",
        initial_user_message="帮我定位文档内容",
        top_k=5,
        page_goal_mode="shadow",
        accepted_pages=list(accepted_pages or []),
        accepted_page_ranges=list(accepted_page_ranges or []),
    )


def _build_result(task: TaskCase, *, predicted_pages: list[int], predicted_titles: list[str]):
    result = build_case_run_result(task, "run-locator-regression")
    result.prediction.predicted_pages = list(predicted_pages)
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=index + 1,
            doc_title=title,
            doc_path=f"/docs/{index + 1}.pdf",
            page_numbers=list(predicted_pages),
        )
        for index, title in enumerate(predicted_titles)
    ]
    return result


def _set_target_docs(task: TaskCase, *targets: TargetDocumentTruth) -> None:
    object.__setattr__(task, "target_docs", list(targets))


def _set_region_groups(target: TargetDocumentTruth, groups: list[dict[str, object]]) -> None:
    object.__setattr__(
        target,
        "accepted_region_groups",
        [
            AcceptedRegionGroup(
                group_id=str(group.get("group_id")) if group.get("group_id") is not None else None,
                page_number=int(group["page_number"]) if group.get("page_number") is not None else None,
                label=str(group.get("label")) if group.get("label") is not None else None,
                boxes_norm=[tuple(box) for box in list(group.get("boxes_norm") or [])],
                match_mode=str(group.get("match_mode") or "any_box"),
            )
            for group in groups
        ],
    )


def _make_target(
    *,
    accepted_pages: list[int],
    accepted_page_ranges: list[tuple[int, int]] | None = None,
    accepted_region_groups: list[dict[str, object]] | None = None,
) -> TargetDocumentTruth:
    target = TargetDocumentTruth(
        file_id="doc-a",
        title="资料A",
        doc_path="/docs/a.pdf",
        accepted_pages=list(accepted_pages),
        accepted_page_ranges=list(accepted_page_ranges or []),
    )
    _set_region_groups(target, list(accepted_region_groups or []))
    return target


def _make_body_search_hit(
    *,
    page_number: int,
    boxes: list[list[float]] | None = None,
    width_px: int | None = 1000,
    height_px: int | None = 1000,
) -> dict[str, object]:
    hit: dict[str, object] = {"page_number": page_number}
    if boxes is not None:
        hit["highlight_boxes_px"] = boxes
    if width_px is not None and height_px is not None:
        hit["metadata"] = {"width_px": width_px, "height_px": height_px}
    return hit


def _apply_body_search_prediction(
    result,
    *,
    predicted_pages: list[int],
    body_search: dict[str, object] | None,
) -> None:
    result.prediction.predicted_pages = list(predicted_pages)
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=1,
            doc_title="资料A",
            doc_path="/docs/a.pdf",
            page_numbers=list(predicted_pages),
            body_search=dict(body_search or {}),
        )
    ]
    result.prediction.locator_source = "body_search" if body_search is not None else None
    result.prediction.locator_status = str(body_search.get("status")) if isinstance(body_search, dict) else None
    result.prediction.locator_top_pages = list(predicted_pages)
    result.prediction.locator_best_page = predicted_pages[0] if predicted_pages else None


def test_normalize_documents_reads_body_search_locator_fields_from_response():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "资料A",
                    "doc_path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [
                            {"page_number": 12},
                            {"page_number": 13},
                        ],
                    },
                }
            ]
        },
    }

    response_type, docs, predicted_pages, page_confidence, locator_summary = normalize_documents(
        "chat_completions",
        body,
    )

    assert response_type == "documents"
    assert page_confidence is None
    assert len(docs) == 1
    assert docs[0].page_numbers == [12, 13]
    assert predicted_pages == [12, 13]
    assert locator_summary["locator_source"] == "body_search"
    assert locator_summary["locator_status"] == "hit"
    assert locator_summary["locator_best_page"] == 12
    assert locator_summary["locator_top_pages"] == [12, 13]
    assert locator_summary["locator_viewer_token_present"] is False
    assert locator_summary["locator_preview_present"] is False


def test_apply_locator_prediction_surfaces_locator_fields_into_standard_result_and_report():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 13)])
    result = build_case_run_result(task, "run-locator-report")
    locator_summary = {
        "locator_source": "body_search",
        "locator_status": "hit",
        "locator_best_page": 12,
        "locator_top_pages": [12, 13],
        "locator_viewer_token_present": True,
        "locator_preview_present": True,
    }

    apply_locator_prediction(result, locator_summary)
    case_dict = result.to_dict()
    suite = TaskSuite(
        split="dev",
        suite_id=task.suite_id,
        layer=task.layer,
        acceptance_threshold=1.0,
        source_files=[],
        cases=[task],
    )
    report = build_score_report(
        split="dev",
        run_id="run-locator-report",
        run_output_dir=None,
        runtime_log_path=None,
        user_strategy="ai",
        user_model=None,
        user_provider=None,
        request_mode="doc_search",
        max_attempts_per_case=None,
        suites=[suite],
        case_results=[result],
        threshold_override=None,
    )

    assert case_dict["prediction"]["locator_source"] == "body_search"
    assert case_dict["prediction"]["locator_status"] == "hit"
    assert case_dict["prediction"]["locator_best_page"] == 12
    assert case_dict["prediction"]["locator_top_pages"] == [12, 13]
    assert report["cases"][0]["prediction"]["locator_source"] == "body_search"
    assert report["cases"][0]["prediction"]["locator_status"] == "hit"
    assert report["cases"][0]["prediction"]["locator_best_page"] == 12
    assert report["cases"][0]["prediction"]["locator_top_pages"] == [12, 13]


def test_judge_locator_reads_prediction_fields_and_marks_hit_at_k():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 13)])
    result = build_case_run_result(task, "run-locator-judge")
    result.metrics.recall_hit = True
    result.prediction.locator_source = "body_search"
    result.prediction.locator_status = "hit"
    result.prediction.locator_best_page = 12
    result.prediction.locator_top_pages = [12, 13]
    result.prediction.locator_viewer_token_present = True
    result.prediction.locator_preview_present = True

    outcome = judge_locator(task, result)

    assert outcome["eligible"] is True
    assert outcome["locator_source"] == "body_search"
    assert outcome["locator_status"] == "hit"
    assert outcome["locator_best_page"] == 12
    assert outcome["locator_top_pages"] == [12, 13]
    assert outcome["locator_hit_at_1"] is True
    assert outcome["locator_hit_at_k"] is True
    assert outcome["locator_exact_page_hit"] is True
    assert outcome["locator_range_overlap_hit"] is True
    assert outcome["document_level_failure"] is None


def test_judge_locator_marks_body_search_missing_after_document_hit():
    task = _build_task(accepted_pages=[12])
    result = build_case_run_result(task, "run-locator-missing")
    result.metrics.recall_hit = True

    outcome = judge_locator(task, result)

    assert outcome["eligible"] is True
    assert outcome["document_hit"] is True
    assert outcome["document_hit_eligible"] is True
    assert outcome["locator_hit_at_1"] is False
    assert outcome["locator_hit_at_k"] is False
    assert outcome["document_level_failure"] == "BODY_SEARCH_MISSING"
    assert "BODY_SEARCH_MISSING" in outcome["warnings"]


def test_aggregate_locator_reports_tracks_conditional_rates_and_failure_buckets():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 13)])
    hit_result = build_case_run_result(task, "run-locator-aggregate-hit", attempt_index=1)
    hit_result.metrics.recall_hit = True
    hit_result.prediction.locator_source = "body_search"
    hit_result.prediction.locator_status = "hit"
    hit_result.prediction.locator_best_page = 12
    hit_result.prediction.locator_top_pages = [12, 13]

    miss_result = build_case_run_result(task, "run-locator-aggregate-miss", attempt_index=2)
    miss_result.metrics.recall_hit = True

    summary = aggregate_locator_reports([hit_result, miss_result])

    assert summary["eligible_cases"] == 2
    assert summary["document_hit_eligible_cases"] == 2
    assert summary["locator_hit_at_1_rate"] == 0.5
    assert summary["locator_hit_at_k_rate"] == 0.5
    assert summary["locator_hit_at_1_given_document_hit_rate"] == 0.5
    assert summary["body_search_missing_count"] == 1
    assert summary["locator_page_miss_count"] == 0


def test_judge_page_uses_accepted_pages_and_ranges_as_only_locator_truth():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 13)])
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(
                file_id="doc-a",
                title="资料A",
                doc_path="/docs/a.pdf",
                accepted_pages=[12],
                accepted_page_ranges=[(12, 13)],
            )
        ],
    )
    result = _build_result(task, predicted_pages=[13], predicted_titles=["资料A"])

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["truth_source"] == "matched_target_docs"
    assert report["page_hit_at_1"] is True
    assert report["page_hit_at_k"] is True
    assert report["exact_page_hit"] is False
    assert report["page_range_overlap_hit"] is True
    assert report["min_page_distance"] == 0


def test_missing_body_search_does_not_create_false_locator_hit_or_pages():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "资料A",
                    "doc_path": "/docs/a.pdf",
                }
            ]
        },
    }

    response_type, docs, predicted_pages, page_confidence, locator_summary = normalize_documents(
        "chat_completions",
        body,
    )

    assert response_type == "documents"
    assert len(docs) == 1
    assert docs[0].page_numbers == []
    assert predicted_pages == []
    assert page_confidence is None
    assert locator_summary["locator_source"] is None
    assert locator_summary["locator_status"] is None
    assert locator_summary["locator_best_page"] is None
    assert locator_summary["locator_top_pages"] == []
    assert locator_summary["locator_viewer_token_present"] is None
    assert locator_summary["locator_preview_present"] is None


def test_legacy_page_fields_remain_compatible_when_body_search_is_absent():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "旧资料A",
                    "doc_path": "/docs/legacy-a.pdf",
                    "page": 11,
                    "page_number": 7,
                    "page_numbers": [7, 8],
                    "pages": [10],
                }
            ]
        },
    }

    response_type, docs, predicted_pages, _, locator_summary = normalize_documents("chat_completions", body)

    assert response_type == "documents"
    assert len(docs) == 1
    assert docs[0].page_numbers == [11, 7, 8, 10]
    assert predicted_pages == [11, 7, 8, 10]
    assert locator_summary["locator_source"] is None


def test_normalize_documents_uses_only_matched_doc_body_search_pages():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "错误资料",
                    "doc_path": "/docs/wrong.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [{"page_number": 12}],
                    },
                },
                {
                    "filename": "资料A",
                    "doc_path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 99},
                        "top_hits": [{"page_number": 99}],
                    },
                },
            ]
        },
    }

    _, docs, predicted_pages, _, locator_summary = normalize_documents(
        "chat_completions",
        body,
        matched_titles=["资料A"],
    )

    assert len(docs) == 2
    assert predicted_pages == [99]
    assert locator_summary["locator_best_page"] == 99
    assert locator_summary["locator_top_pages"] == [99]


def test_judge_page_does_not_accept_pages_polluted_by_other_documents():
    task = _build_task(accepted_pages=[12])
    target = _make_target(accepted_pages=[12])
    _set_target_docs(task, target)

    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "错误资料",
                    "doc_path": "/docs/wrong.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [{"page_number": 12}],
                    },
                },
                {
                    "filename": "资料A",
                    "doc_path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 99},
                        "top_hits": [{"page_number": 99}],
                    },
                },
            ]
        },
    }

    _, docs, predicted_pages, _, locator_summary = normalize_documents(
        "chat_completions",
        body,
        matched_titles=["资料A"],
    )
    result = build_case_run_result(task, "run-page-pollution")
    result.metrics.recall_hit = True
    result.prediction.top_k_documents = docs
    result.prediction.predicted_pages = predicted_pages
    apply_locator_prediction(result, locator_summary)

    report = judge_page(task, result)

    assert report["eligible"] is True
    assert report["page_hit_at_k"] is False
    assert report["page_hit_at_1"] is False


def test_judge_page_stays_miss_when_target_document_is_not_recalled():
    task = _build_task(accepted_pages=[12])
    target = _make_target(accepted_pages=[12])
    _set_target_docs(task, target)

    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "错误资料",
                    "doc_path": "/docs/wrong.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [{"page_number": 12}],
                    },
                }
            ]
        },
    }

    _, docs, predicted_pages, _, locator_summary = normalize_documents(
        "chat_completions",
        body,
        target_docs=list(task.target_docs),
        matched_titles=["资料A"],
    )
    result = build_case_run_result(task, "run-page-complete-miss")
    result.metrics.recall_hit = False
    result.prediction.top_k_documents = docs
    result.prediction.predicted_pages = predicted_pages
    apply_locator_prediction(result, locator_summary)

    report = judge_page(task, result)

    assert predicted_pages == []
    assert report["eligible"] is True
    assert report["page_hit_at_k"] is False
    assert report["page_hit_at_1"] is False


def test_normalize_documents_filters_body_search_pages_with_compatible_identity_fields():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "错误资料",
                    "doc_path": "/docs/wrong.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [{"page_number": 12}],
                    },
                },
                {
                    "name": "资料A",
                    "path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 99},
                        "top_hits": [{"page_number": 99}],
                    },
                },
            ]
        },
    }

    _, docs, predicted_pages, _, locator_summary = normalize_documents(
        "chat_completions",
        body,
        matched_titles=["资料A"],
    )

    assert len(docs) == 2
    assert docs[1].doc_title == "资料A"
    assert docs[1].doc_path == "/docs/a.pdf"
    assert predicted_pages == [99]
    assert locator_summary["locator_best_page"] == 99
    assert locator_summary["locator_top_pages"] == [99]


def test_normalize_documents_uses_first_matched_document_instead_of_merging_multiple_matches():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "name": "资料A-旧版本",
                    "path": "/docs/a-old.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [{"page_number": 12}],
                    },
                },
                {
                    "name": "资料A",
                    "path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 99},
                        "top_hits": [{"page_number": 99}],
                    },
                },
            ]
        },
    }

    _, _, predicted_pages, _, locator_summary = normalize_documents(
        "chat_completions",
        body,
        matched_titles=["资料A"],
    )

    assert predicted_pages == [99]
    assert locator_summary["locator_best_page"] == 99
    assert locator_summary["locator_top_pages"] == [99]


def test_locator_and_coord_ignore_empty_doc_path_before_real_target():
    task = _build_task(accepted_pages=[12])
    target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-12",
                "page_number": 12,
                "label": "目标区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(task, target)
    result = build_case_run_result(task, "run-empty-doc-path")
    result.metrics.recall_hit = True
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=1,
            doc_title="错误资料",
            doc_path="",
            page_numbers=[99],
            body_search={
                "status": "hit",
                "best_hit": _make_body_search_hit(page_number=99, boxes=[[700, 700, 800, 800]]),
                "top_hits": [_make_body_search_hit(page_number=99, boxes=[[700, 700, 800, 800]])],
            },
        ),
        PredictedDocument(
            rank=2,
            doc_title="资料A",
            doc_path="/docs/a.pdf",
            page_numbers=[12],
            body_search={
                "status": "hit",
                "best_hit": _make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]]),
                "top_hits": [_make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]])],
            },
        ),
    ]
    result.prediction.predicted_pages = [12]

    locator_outcome = judge_locator(task, result)
    coord_outcome = judge_coord(task, result)

    assert locator_outcome["locator_best_page"] == 12
    assert locator_outcome["locator_hit_at_k"] is True
    assert coord_outcome["coord_hit"] is True
    assert coord_outcome["coord_hit_page_numbers"] == [12]


def test_judge_locator_uses_target_doc_path_when_titles_are_absent():
    task = _build_task(accepted_pages=[12])
    object.__setattr__(task, "accepted_titles", [])
    target = TargetDocumentTruth(
        file_id="doc-a",
        title=None,
        doc_path="/docs/a.pdf",
        accepted_pages=[12],
        accepted_page_ranges=[],
    )
    _set_target_docs(task, target)
    result = build_case_run_result(task, "run-path-only-locator")
    result.metrics.recall_hit = True
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=1,
            doc_title="错误资料",
            doc_path="/docs/wrong.pdf",
            page_numbers=[99],
            body_search={
                "status": "hit",
                "best_hit": {"page_number": 99},
                "top_hits": [{"page_number": 99}],
            },
        ),
        PredictedDocument(
            rank=2,
            doc_title="任意标题",
            doc_path="/docs/a.pdf",
            page_numbers=[12],
            body_search={
                "status": "hit",
                "best_hit": {"page_number": 12},
                "top_hits": [{"page_number": 12}],
            },
        ),
    ]

    outcome = judge_locator(task, result)

    assert outcome["locator_best_page"] == 12
    assert outcome["locator_hit_at_k"] is True
    assert outcome["document_level_failure"] is None


def test_normalize_documents_keeps_pages_from_all_matched_targets_in_any_of_mode():
    task = _build_task(accepted_pages=[12, 99])
    target_a = TargetDocumentTruth(
        file_id="doc-a",
        title="资料A",
        doc_path="/docs/a.pdf",
        accepted_pages=[99],
        accepted_page_ranges=[],
    )
    target_b = TargetDocumentTruth(
        file_id="doc-b",
        title="资料B",
        doc_path="/docs/b.pdf",
        accepted_pages=[12],
        accepted_page_ranges=[],
    )
    _set_target_docs(task, target_a, target_b)

    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "资料A",
                    "doc_path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 99},
                        "top_hits": [{"page_number": 99}],
                    },
                },
                {
                    "filename": "资料B",
                    "doc_path": "/docs/b.pdf",
                    "body_search": {
                        "status": "hit",
                        "best_hit": {"page_number": 12},
                        "top_hits": [{"page_number": 12}],
                    },
                },
            ]
        },
    }

    _, docs, predicted_pages, _, locator_summary = normalize_documents(
        "chat_completions",
        body,
        target_docs=list(task.target_docs),
        matched_titles=["资料A", "资料B"],
    )

    assert len(docs) == 2
    assert predicted_pages == [99, 12]
    assert locator_summary["locator_top_pages"] == [99, 12]


def test_locator_and_coord_accept_later_hit_in_multi_target_any_of():
    task = _build_task(accepted_pages=[12, 99])
    target_a = _make_target(
        accepted_pages=[99],
        accepted_region_groups=[
            {
                "group_id": "region-a",
                "page_number": 99,
                "label": "资料A区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    object.__setattr__(target_a, "title", "资料A")
    object.__setattr__(target_a, "doc_path", "/docs/a.pdf")
    target_b = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-b",
                "page_number": 12,
                "label": "资料B区域",
                "boxes_norm": [[0.30, 0.30, 0.40, 0.40]],
                "match_mode": "any_box",
            }
        ],
    )
    object.__setattr__(target_b, "file_id", "doc-b")
    object.__setattr__(target_b, "title", "资料B")
    object.__setattr__(target_b, "doc_path", "/docs/b.pdf")
    _set_target_docs(task, target_a, target_b)

    result = build_case_run_result(task, "run-multi-target-any-of")
    result.metrics.recall_hit = True
    result.metrics.matched_targets = ["资料A", "资料B"]
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=1,
            doc_title="资料A",
            doc_path="/docs/a.pdf",
            page_numbers=[99],
            body_search={
                "status": "hit",
                "best_hit": _make_body_search_hit(page_number=99, boxes=[[700, 700, 800, 800]]),
                "top_hits": [_make_body_search_hit(page_number=99, boxes=[[700, 700, 800, 800]])],
            },
        ),
        PredictedDocument(
            rank=2,
            doc_title="资料B",
            doc_path="/docs/b.pdf",
            page_numbers=[12],
            body_search={
                "status": "hit",
                "best_hit": _make_body_search_hit(page_number=12, boxes=[[300, 300, 400, 400]]),
                "top_hits": [_make_body_search_hit(page_number=12, boxes=[[300, 300, 400, 400]])],
            },
        ),
    ]
    result.prediction.predicted_pages = [99, 12]
    result.prediction.locator_source = "body_search"
    result.prediction.locator_status = "hit"
    result.prediction.locator_best_page = 99
    result.prediction.locator_top_pages = [99, 12]
    result.prediction.locator_viewer_token_present = False
    result.prediction.locator_preview_present = False
    result.prediction.coord_predicted_page_numbers = [99, 12]
    result.prediction.coord_predicted_boxes_norm = [
        RegionPageBoxes(page_number=99, boxes=[(0.70, 0.70, 0.80, 0.80)]),
        RegionPageBoxes(page_number=12, boxes=[(0.30, 0.30, 0.40, 0.40)]),
    ]

    locator_outcome = judge_locator(task, result)
    coord_outcome = judge_coord(task, result)

    assert locator_outcome["locator_hit_at_k"] is True
    assert coord_outcome["coord_hit"] is True
    assert coord_outcome["coord_hit_page_numbers"] == [12]
    assert coord_outcome["coord_hit_group_ids"] == ["region-b"]


def test_judge_coord_accepts_multi_page_any_of_hit():
    task = _build_task(accepted_pages=[12, 13], accepted_page_ranges=[(12, 13)])
    target = _make_target(
        accepted_pages=[12, 13],
        accepted_page_ranges=[(12, 13)],
        accepted_region_groups=[
            {
                "group_id": "page12-group",
                "page_number": 12,
                "label": "第一页区域",
                "boxes_norm": [[0.10, 0.10, 0.16, 0.16]],
                "match_mode": "any_box",
            },
            {
                "group_id": "page13-group",
                "page_number": 13,
                "label": "第二页区域",
                "boxes_norm": [[0.40, 0.40, 0.50, 0.50]],
                "match_mode": "any_box",
            },
        ],
    )
    _set_target_docs(task, target)
    result = build_case_run_result(task, "run-coord-any-of")
    result.metrics.recall_hit = True
    _apply_body_search_prediction(
        result,
        predicted_pages=[12, 13],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[10, 10, 40, 40]]),
            "top_hits": [
                _make_body_search_hit(page_number=12, boxes=[[10, 10, 40, 40]]),
                _make_body_search_hit(page_number=13, boxes=[[410, 410, 490, 490]]),
            ],
        },
    )

    outcome = judge_coord(task, result)

    assert outcome["eligible"] is True
    assert outcome["doc_hit"] is True
    assert outcome["page_hit"] is True
    assert outcome["coord_hit"] is True
    assert outcome["coord_failure_reason"] is None
    assert outcome["coord_hit_page_numbers"] == [13]
    assert outcome["coord_hit_group_ids"] == ["page13-group"]


def test_judge_coord_respects_document_gate_before_page_and_region_checks():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 12)])
    target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-12",
                "page_number": 12,
                "label": "目标区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(task, target)
    result = build_case_run_result(task, "run-coord-doc-gate")
    result.metrics.recall_hit = False
    _apply_body_search_prediction(
        result,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]]),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]])],
        },
    )

    outcome = judge_coord(task, result)

    assert outcome["eligible"] is True
    assert outcome["doc_hit"] is False
    assert outcome["page_hit"] is False
    assert outcome["coord_hit"] is False
    assert outcome["coord_failure_reason"] == "DOC_RECALL_MISS"


def test_judge_coord_respects_page_gate_before_coord_compare():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 12)])
    target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-12",
                "page_number": 12,
                "label": "目标区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(task, target)
    result = build_case_run_result(task, "run-coord-page-gate")
    result.metrics.recall_hit = True
    _apply_body_search_prediction(
        result,
        predicted_pages=[99],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=99, boxes=[[100, 100, 200, 200]]),
            "top_hits": [_make_body_search_hit(page_number=99, boxes=[[100, 100, 200, 200]])],
        },
    )

    outcome = judge_coord(task, result)

    assert outcome["eligible"] is True
    assert outcome["doc_hit"] is True
    assert outcome["page_hit"] is False
    assert outcome["coord_hit"] is False
    assert outcome["coord_failure_reason"] == "PAGE_RECALL_MISS"


def test_judge_coord_accepts_multi_box_region_group_when_any_box_matches():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 12)])
    target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "multi-box-group",
                "page_number": 12,
                "label": "多框区域",
                "boxes_norm": [
                    [0.10, 0.10, 0.16, 0.16],
                    [0.30, 0.30, 0.36, 0.36],
                ],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(task, target)
    result = build_case_run_result(task, "run-coord-multi-box")
    result.metrics.recall_hit = True
    _apply_body_search_prediction(
        result,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[305, 305, 355, 355]]),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[305, 305, 355, 355]])],
        },
    )

    outcome = judge_coord(task, result)

    assert outcome["coord_hit"] is True
    assert outcome["coord_hit_group_ids"] == ["multi-box-group"]
    assert outcome["coord_failure_reason"] is None


def test_judge_coord_distinguishes_failure_reasons():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 12)])
    target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-12",
                "page_number": 12,
                "label": "目标区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(task, target)

    missing_body_search = build_case_run_result(task, "run-coord-body-missing", attempt_index=1)
    missing_body_search.metrics.recall_hit = True
    missing_body_search.prediction.predicted_pages = [12]

    missing_metadata = build_case_run_result(task, "run-coord-metadata-missing", attempt_index=2)
    missing_metadata.metrics.recall_hit = True
    _apply_body_search_prediction(
        missing_metadata,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]], width_px=None, height_px=None),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]], width_px=None, height_px=None)],
        },
    )

    missing_boxes = build_case_run_result(task, "run-coord-box-missing", attempt_index=3)
    missing_boxes.metrics.recall_hit = True
    _apply_body_search_prediction(
        missing_boxes,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=None),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=None)],
        },
    )

    region_miss = build_case_run_result(task, "run-coord-region-miss", attempt_index=4)
    region_miss.metrics.recall_hit = True
    _apply_body_search_prediction(
        region_miss,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[600, 600, 700, 700]]),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[600, 600, 700, 700]])],
        },
    )

    assert judge_coord(task, missing_body_search)["coord_failure_reason"] == "BODY_SEARCH_MISSING"
    assert judge_coord(task, missing_metadata)["coord_failure_reason"] == "COORD_METADATA_MISSING"
    assert judge_coord(task, missing_boxes)["coord_failure_reason"] == "COORD_BOX_MISSING"
    assert judge_coord(task, region_miss)["coord_failure_reason"] == "COORD_REGION_MISS"


def test_coord_rollup_in_build_score_report_surfaces_required_rates_and_failure_counts():
    task = _build_task(accepted_pages=[12], accepted_page_ranges=[(12, 12)])
    target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-12",
                "page_number": 12,
                "label": "目标区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(task, target)
    suite = TaskSuite(
        split="dev",
        suite_id=task.suite_id,
        layer=task.layer,
        acceptance_threshold=1.0,
        source_files=[],
        cases=[task],
    )

    hit_result = build_case_run_result(task, "run-coord-rollup", attempt_index=1)
    hit_result.metrics.recall_hit = True
    _apply_body_search_prediction(
        hit_result,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]]),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]])],
        },
    )

    page_miss_result = build_case_run_result(task, "run-coord-rollup", attempt_index=2)
    page_miss_result.metrics.recall_hit = True
    _apply_body_search_prediction(
        page_miss_result,
        predicted_pages=[99],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=99, boxes=[[100, 100, 200, 200]]),
            "top_hits": [_make_body_search_hit(page_number=99, boxes=[[100, 100, 200, 200]])],
        },
    )

    doc_miss_result = build_case_run_result(task, "run-coord-rollup", attempt_index=3)
    doc_miss_result.metrics.recall_hit = False

    metadata_miss_result = build_case_run_result(task, "run-coord-rollup", attempt_index=4)
    metadata_miss_result.metrics.recall_hit = True
    _apply_body_search_prediction(
        metadata_miss_result,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]], width_px=None, height_px=None),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]], width_px=None, height_px=None)],
        },
    )

    report = build_score_report(
        split="dev",
        run_id="run-coord-rollup",
        run_output_dir=None,
        runtime_log_path=None,
        user_strategy="ai",
        user_model=None,
        user_provider=None,
        request_mode="doc_search",
        max_attempts_per_case=None,
        suites=[suite],
        case_results=[hit_result, page_miss_result, doc_miss_result, metadata_miss_result],
        threshold_override=None,
    )
    summary = report["summary"]["coord"]

    assert summary["coord_hit_rate"] == 0.25
    assert summary["coord_hit_given_doc_hit_rate"] == 0.333333
    assert summary["coord_hit_given_page_hit_rate"] == 0.5
    assert summary["coord_failure_reason_counts"] == {
        "COORD_METADATA_MISSING": 1,
        "DOC_RECALL_MISS": 1,
        "PAGE_RECALL_MISS": 1,
    }

    direct_summary = aggregate_coord_reports([hit_result, page_miss_result, doc_miss_result, metadata_miss_result], task_lookup={
        ("dev", task.suite_id, task.case_id): task,
    })
    assert direct_summary["coord_hit_rate"] == 0.25


def test_case_level_coord_rollup_uses_conditional_denominator():
    hit_task = _build_task(accepted_pages=[12])
    hit_target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-hit",
                "page_number": 12,
                "label": "命中区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    _set_target_docs(hit_task, hit_target)
    hit_result = build_case_run_result(hit_task, "run-case-rollup-denominator", attempt_index=1)
    hit_result.metrics.recall_hit = True
    _apply_body_search_prediction(
        hit_result,
        predicted_pages=[12],
        body_search={
            "status": "hit",
            "best_hit": _make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]]),
            "top_hits": [_make_body_search_hit(page_number=12, boxes=[[100, 100, 200, 200]])],
        },
    )

    miss_task = _build_task(accepted_pages=[12])
    miss_target = _make_target(
        accepted_pages=[12],
        accepted_region_groups=[
            {
                "group_id": "region-miss",
                "page_number": 12,
                "label": "未命中区域",
                "boxes_norm": [[0.10, 0.10, 0.20, 0.20]],
                "match_mode": "any_box",
            }
        ],
    )
    object.__setattr__(miss_task, "case_id", "locator_regression_case_002")
    _set_target_docs(miss_task, miss_target)
    miss_result = build_case_run_result(miss_task, "run-case-rollup-denominator", attempt_index=1)
    miss_result.metrics.recall_hit = False

    case_rollups = build_case_rollups([hit_result, miss_result], task_lookup={
        ("dev", hit_task.suite_id, hit_task.case_id): hit_task,
        ("dev", miss_task.suite_id, miss_task.case_id): miss_task,
    })
    summary = aggregate_case_rollup_coord(case_rollups)

    assert summary["eligible_cases"] == 2
    assert summary["doc_hit_cases"] == 1
    assert summary["page_hit_cases"] == 1
    assert summary["coord_hit_rate"] == 0.5
    assert summary["coord_hit_given_doc_hit_rate"] == 1.0
    assert summary["coord_hit_given_page_hit_rate"] == 1.0


def test_build_score_report_uses_unique_case_count_for_top_level_and_suite():
    task = _build_task(accepted_pages=[12])
    suite = TaskSuite(
        split="dev",
        suite_id=task.suite_id,
        layer=task.layer,
        acceptance_threshold=1.0,
        source_files=[],
        cases=[task],
    )
    first = build_case_run_result(task, "run-case-count", attempt_index=1)
    second = build_case_run_result(task, "run-case-count", attempt_index=2)

    report = build_score_report(
        split="dev",
        run_id="run-case-count",
        run_output_dir=None,
        runtime_log_path=None,
        user_strategy="ai",
        user_model=None,
        user_provider=None,
        request_mode="doc_search",
        max_attempts_per_case=None,
        suites=[suite],
        case_results=[first, second],
        threshold_override=None,
    )

    assert report["attempt_count"] == 2
    assert report["unique_case_count"] == 1
    assert report["case_count"] == 1
    assert report["suite_summaries"][0]["attempt_count"] == 2
    assert report["suite_summaries"][0]["unique_case_count"] == 1
    assert report["suite_summaries"][0]["case_count"] == 1
