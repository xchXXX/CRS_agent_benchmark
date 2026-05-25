from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.chat_export.render_case_review_html import build_html
from doc_search_bench.types import (
    AcceptedRegionGroup,
    PredictedDocument,
    RegionPageBoxes,
    TargetDocumentTruth,
    TaskCase,
    TaskSuite,
    UserProfile,
    build_case_run_result,
)
from doc_search_bench.run import build_actual_report, build_score_report


def _build_task() -> TaskCase:
    return TaskCase(
        case_id="case_render_001",
        split="dev",
        layer="component",
        suite_id="suite_render",
        input_modality="text",
        question_text="帮我定位这份资料的页码和区域",
        question_images=[],
        vehicle_info=None,
        preprocess_strategy="none",
        benchmark_track="chat_completions",
        request_context={},
        accepted_titles=["资料A"],
        preferred_title=None,
        user_id="benchmark_user",
        instruction="请返回资料位置。",
        initial_user_message="帮我定位这份资料的页码和区域",
        page_goal_mode="shadow",
        accepted_pages=[12],
        accepted_page_ranges=[(12, 13)],
        user_profile=UserProfile(
            persona="normal",
            goal="确认资料位置",
            known_items=["资料A", "页码定位"],
            uncertain_items=["具体框位置"],
        ),
        target_docs=[
            TargetDocumentTruth(
                file_id="doc-a",
                title="资料A",
                doc_path="/docs/a.pdf",
                facets={"brand": "东风"},
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
        metadata={
            "evidence_source": "db_room_export",
            "room_id": "room-001",
            "opening_message_id": "msg-open-001",
            "answer_message_id": "msg-answer-001",
            "transcript": [
                {
                    "id": 1,
                    "chat_from": "用户A",
                    "msgtype": "text",
                    "text": "帮我定位这份资料",
                    "msg_date": "2026-05-22 10:00:00",
                },
                {
                    "id": 2,
                    "chat_from": "GongGui02",
                    "msgtype": "text",
                    "text": "返回资料A",
                    "msg_date": "2026-05-22 10:01:00",
                },
            ],
        },
    )


def _build_reports() -> tuple[dict, dict]:
    task = _build_task()
    result = build_case_run_result(task, "run-render")
    result.metrics.recall_hit = True
    result.metrics.hit_at_1 = True
    result.metrics.hit_at_3 = True
    result.metrics.mrr = 1.0
    result.response.final_status = "success_documents"
    result.prediction.top_k_documents = [
        PredictedDocument(
            rank=1,
            doc_title="资料A",
            doc_path="/docs/a.pdf",
            score=0.98,
            page_numbers=[12, 13],
        )
    ]
    result.prediction.predicted_pages = [12, 13]
    result.metrics.page_hit_at_k = True
    result.metrics.page_hit_at_1 = True
    result.metrics.exact_page_hit = True
    result.metrics.page_range_overlap_hit = True
    result.metrics.min_page_distance = 0
    result.metrics.locator_status = "hit"
    result.metrics.locator_best_page = 12
    result.metrics.locator_top_pages = [12, 13]
    result.metrics.coord_eligible = True
    result.metrics.coord_hit = True
    result.metrics.coord_hit_group_ids = ["region_001"]
    result.metrics.coord_hit_page_numbers = [12]
    result.metrics.coord_failure_reason = None
    result.metrics.coord_metadata_present = True
    result.metrics.coord_viewer_token_present = True
    result.prediction.coord_predicted_page_numbers = [12]
    result.prediction.coord_predicted_boxes_norm = [
        RegionPageBoxes(page_number=12, boxes=[(0.11, 0.21, 0.31, 0.41)])
    ]
    result.prediction.coord_predicted_boxes_px = [
        RegionPageBoxes(page_number=12, boxes=[(110.0, 210.0, 310.0, 410.0)])
    ]
    result.prediction.coord_viewer_token = "viewer-token-001"
    result.prediction.coord_metadata_present = True

    suite = TaskSuite(
        split="dev",
        suite_id=task.suite_id,
        layer=task.layer,
        acceptance_threshold=1.0,
        source_files=[],
        cases=[task],
    )
    actual_report = build_actual_report(
        split="dev",
        run_id="run-render",
        run_output_dir=None,
        runtime_log_path=None,
        user_strategy="human",
        user_model=None,
        user_provider=None,
        request_mode="doc_search",
        max_attempts_per_case=1,
        suites=[suite],
        case_results=[result],
    )
    score_report = build_score_report(
        split="dev",
        run_id="run-render",
        run_output_dir=None,
        runtime_log_path=None,
        user_strategy="human",
        user_model=None,
        user_provider=None,
        request_mode="doc_search",
        max_attempts_per_case=1,
        suites=[suite],
        case_results=[result],
        threshold_override=None,
    )
    return actual_report, score_report


def test_build_html_renders_chinese_summary_for_page_and_coord_levels(tmp_path: Path):
    actual_report, score_report = _build_reports()

    html = build_html(
        cases=actual_report["cases"],
        gold_map={},
        output_path=tmp_path / "case_review.html",
        score_cases=score_report["cases"],
    )

    assert "标准答案" in html
    assert "实际返回" in html
    assert "页级结果" in html
    assert "坐标级结果" in html
    assert "标准答案页码" in html
    assert "实际返回页码" in html
    assert "标准答案区域" in html
    assert "实际返回坐标框" in html
    assert "坐标是否命中" in html
    assert "聊天轨迹" in html
    assert "原始返回" in html


def test_build_html_uses_answer_and_actual_return_without_verbose_internal_metric_labels(tmp_path: Path):
    actual_report, score_report = _build_reports()

    html = build_html(
        cases=actual_report["cases"],
        gold_map={},
        output_path=tmp_path / "case_review.html",
        score_cases=score_report["cases"],
    )

    assert '<div class="section-title">页级结果</div>' in html
    assert '<div class="kv-label">标准答案页码</div>' in html
    assert '<div class="kv-label">实际返回页码</div>' in html
    assert '<div class="kv-label">标准答案区域</div>' in html
    assert '<div class="kv-label">实际返回坐标框</div>' in html
