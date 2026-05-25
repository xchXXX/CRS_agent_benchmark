from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.run import build_score_report
from doc_search_bench.types import (
    AcceptedRegionGroup,
    RegionPageBoxes,
    TargetDocumentTruth,
    TaskCase,
    TaskSuite,
    build_case_run_result,
    merge_suite_from_paths,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_task() -> TaskCase:
    return TaskCase(
        case_id="locator_case_001",
        split="dev",
        layer="component",
        suite_id="suite_locator",
        input_modality="text",
        question_text="帮我定位这份资料的页码",
        question_images=[],
        vehicle_info=None,
        preprocess_strategy="none",
        benchmark_track="chat_completions",
        request_context={},
        accepted_titles=["资料A"],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="请返回正确页码。",
        initial_user_message="帮我定位这份资料的页码",
        top_k=5,
        page_goal_mode="shadow",
        target_docs=[
            TargetDocumentTruth(
                file_id="doc-a",
                title="资料A",
                doc_path="/docs/a.pdf",
                facets={"brand": "东风", "doc_type": "电路图"},
                accepted_pages=[12],
                accepted_page_ranges=[(12, 13)],
                accepted_region_groups=[
                    AcceptedRegionGroup(
                        group_id="region_001",
                        page_number=12,
                        label="油门踏板",
                        boxes_norm=[(0.1, 0.2, 0.3, 0.4)],
                        match_mode="any_box",
                    )
                ],
            )
        ],
    )


def test_merge_suite_from_paths_uses_accepted_pages_as_unique_locator_truth_and_ignores_legacy_fields(
    tmp_path: Path,
):
    fixture_path = tmp_path / "fixture.json"
    gold_path = tmp_path / "gold.json"
    _write_json(
        fixture_path,
        {
            "suite_id": "suite_locator",
            "layer": "component",
            "source_files": ["source.json"],
            "cases": [
                {
                    "case_id": "locator_case_001",
                    "input_modality": "text",
                    "question_text": "帮我定位这份资料的页码",
                    "question_images": [],
                    "vehicle_info": None,
                    "preprocess_strategy": "none",
                    "benchmark_track": "chat_completions",
                    "request_context": {},
                    "user_id": "benchmark_user",
                    "instruction": "请返回正确页码。",
                    "initial_user_message": "帮我定位这份资料的页码",
                }
            ],
        },
    )
    _write_json(
        gold_path,
        {
            "acceptance_threshold": 1.0,
            "cases": [
                {
                    "case_id": "locator_case_001",
                    "target_docs": [
                        {
                            "file_id": "doc-a",
                            "title": "资料A",
                            "doc_path": "/docs/a.pdf",
                            "facets": {
                                "brand": "东风",
                                "doc_type": "电路图"
                            },
                            "accepted_pages": [12],
                            "accepted_page_ranges": [[12, 13]],
                            "accepted_locator_pages": [99],
                            "accepted_locator_page_ranges": [[99, 100]],
                            "accepted_region_groups": [
                                {
                                    "group_id": "region_001",
                                    "page_number": 12,
                                    "label": "油门踏板",
                                    "boxes_norm": [
                                        [0.1, 0.2, 0.3, 0.4],
                                        [0.31, 0.2, 0.42, 0.4],
                                    ],
                                    "match_mode": "any_box",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )

    suite = merge_suite_from_paths(split="dev", fixture_path=fixture_path, gold_path=gold_path)
    task = suite.cases[0]

    assert task.page_goal_mode == "shadow"
    assert len(task.target_docs) == 1
    assert task.target_docs[0] == TargetDocumentTruth(
        file_id="doc-a",
        title="资料A",
        doc_path="/docs/a.pdf",
        facets={"brand": "东风", "doc_type": "电路图"},
        accepted_pages=[12],
        accepted_page_ranges=[(12, 13)],
        accepted_region_groups=[
            AcceptedRegionGroup(
                group_id="region_001",
                page_number=12,
                label="油门踏板",
                boxes_norm=[
                    (0.1, 0.2, 0.3, 0.4),
                    (0.31, 0.2, 0.42, 0.4),
                ],
                match_mode="any_box",
            )
        ],
    )
    assert task.accepted_pages == [12]
    assert task.accepted_page_ranges == [(12, 13)]
    result = build_case_run_result(task, "run-locator")
    assert result.task_metadata.accepted_region_groups == [
        AcceptedRegionGroup(
            group_id="region_001",
            page_number=12,
            label="油门踏板",
            boxes_norm=[
                (0.1, 0.2, 0.3, 0.4),
                (0.31, 0.2, 0.42, 0.4),
            ],
            match_mode="any_box",
        )
    ]
    assert result.task_metadata.coord_gold_page_numbers == [12]
    assert result.task_metadata.coord_gold_group_ids == ["region_001"]


def test_merge_suite_from_paths_allows_target_docs_without_region_groups(
    tmp_path: Path,
):
    fixture_path = tmp_path / "fixture_no_region_groups.json"
    gold_path = tmp_path / "gold_no_region_groups.json"
    _write_json(
        fixture_path,
        {
            "suite_id": "suite_locator_no_region_groups",
            "layer": "component",
            "source_files": ["source.json"],
            "cases": [
                {
                    "case_id": "locator_case_no_region_groups_001",
                    "input_modality": "text",
                    "question_text": "帮我找资料",
                    "question_images": [],
                    "vehicle_info": None,
                    "preprocess_strategy": "none",
                    "benchmark_track": "chat_completions",
                    "request_context": {},
                    "user_id": "benchmark_user",
                    "instruction": "请返回资料。",
                }
            ],
        },
    )
    _write_json(
        gold_path,
        {
            "acceptance_threshold": 1.0,
            "cases": [
                {
                    "case_id": "locator_case_no_region_groups_001",
                    "target_docs": [
                        {
                            "file_id": "doc-legacy",
                            "title": "旧资料A",
                            "doc_path": "/docs/legacy.pdf",
                            "accepted_pages": [7],
                        }
                    ],
                }
            ],
        },
    )

    suite = merge_suite_from_paths(split="dev", fixture_path=fixture_path, gold_path=gold_path)
    task = suite.cases[0]
    result = build_case_run_result(task, "run-legacy")

    assert len(task.target_docs) == 1
    assert task.target_docs[0].accepted_region_groups == []
    assert result.task_metadata.accepted_region_groups == []
    assert result.task_metadata.coord_gold_page_numbers == []
    assert result.task_metadata.coord_gold_group_ids == []


def test_standard_result_and_score_report_surface_locator_fields():
    task = _build_task()
    result = build_case_run_result(task, "run-locator")
    result.metrics.recall_hit = True
    result.metrics.hit_at_1 = True
    result.metrics.hit_at_3 = True
    result.metrics.mrr = 1.0
    result.prediction.predicted_pages = [12, 13]
    result.prediction.top_k_documents = []
    result.response.final_status = "success_documents"

    result.metrics.locator_status = "body_search"
    result.metrics.locator_best_page = 12
    result.metrics.locator_top_pages = [12, 13]
    result.metrics.locator_source = "body_search"
    result.prediction.locator_status = "body_search"
    result.prediction.locator_best_page = 12
    result.prediction.locator_top_pages = [12, 13]
    result.prediction.locator_source = "body_search"
    result.prediction.coord_predicted_page_numbers = [12]
    result.prediction.coord_predicted_boxes_px = [
        RegionPageBoxes(page_number=12, boxes=[(100.0, 50.0, 300.0, 150.0)])
    ]
    result.prediction.coord_predicted_boxes_norm = [
        RegionPageBoxes(page_number=12, boxes=[(0.1, 0.1, 0.3, 0.3)])
    ]
    result.prediction.coord_viewer_token = "viewer-token-001"
    result.prediction.coord_metadata_present = True
    result.metrics.coord_metadata_present = True
    result.metrics.coord_viewer_token_present = True

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
        run_id="run-locator",
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

    assert case_dict["prediction"]["locator_status"] == "body_search"
    assert case_dict["prediction"]["locator_best_page"] == 12
    assert case_dict["prediction"]["locator_top_pages"] == [12, 13]
    assert case_dict["prediction"]["locator_source"] == "body_search"
    assert case_dict["prediction"]["coord_predicted_page_numbers"] == [12]
    assert case_dict["prediction"]["coord_predicted_boxes_norm"] == [
        {"page_number": 12, "boxes": [(0.1, 0.1, 0.3, 0.3)]}
    ]
    assert case_dict["prediction"]["coord_viewer_token"] == "viewer-token-001"
    assert case_dict["prediction"]["coord_metadata_present"] is True
    assert case_dict["task_metadata"]["accepted_region_groups"] == [
        {
            "group_id": "region_001",
            "page_number": 12,
            "label": "油门踏板",
            "boxes_norm": [(0.1, 0.2, 0.3, 0.4)],
            "match_mode": "any_box",
        }
    ]
    assert case_dict["task_metadata"]["coord_gold_page_numbers"] == [12]
    assert case_dict["task_metadata"]["coord_gold_group_ids"] == ["region_001"]
    assert report["cases"][0]["prediction"]["locator_status"] == "body_search"
    assert report["cases"][0]["prediction"]["locator_best_page"] == 12
    assert report["cases"][0]["prediction"]["locator_top_pages"] == [12, 13]
    assert report["cases"][0]["prediction"]["locator_source"] == "body_search"
    assert report["cases"][0]["prediction"]["coord_predicted_page_numbers"] == [12]
    assert report["cases"][0]["prediction"]["coord_metadata_present"] is True
