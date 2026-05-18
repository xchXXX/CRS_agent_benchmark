import json
from pathlib import Path

from openpyxl import load_workbook

from app.benchmark.doc_search import (
    benchmark_case_status,
    enrich_predictions_with_cases,
    evaluate_predictions,
    write_excel_report,
)


def test_benchmark_case_status_uses_gold_answerable_for_no_answer_cases():
    no_answer_case = {
        "case_id": "case_1",
        "input": {"question_text": "老师这个车的资料也找不到"},
        "gold": {"answerable": False, "acceptable_doc_names": []},
    }
    prediction = {
        "case_id": "case_1",
        "answerable": True,
        "results": [{"rank": 1, "doc_name": "重汽豪泺_2012版ZZ1257N4048W_罐车线束图"}],
        "results_scored": [{"rank": 1, "doc_name": "重汽豪泺_2012版ZZ1257N4048W_罐车线束图"}],
        "runtime": {},
    }

    assert benchmark_case_status(no_answer_case, prediction) == "无资料误召回"


def test_enrich_predictions_backfills_case_fields():
    cases = [
        {
            "case_id": "case_1",
            "input": {
                "question_text": "老师这个东风多利卡D6的仪表针脚有吗",
                "image_paths": ["/tmp/case_1.jpg"],
            },
            "gold": {"answerable": False, "acceptable_doc_names": []},
        }
    ]
    predictions = [
        {
            "case_id": "case_1",
            "track": "production_flow",
            "answerable": True,
            "results": [{"rank": 1, "doc_name": "东风多利卡_仪表针脚定义"}],
            "results_scored": [{"rank": 1, "doc_name": "东风多利卡_仪表针脚定义"}],
            "image_evidence": [{"summary": "图片包含东风多利卡D6仪表盘及背面标签。"}],
            "runtime": {},
        }
    ]

    enriched = enrich_predictions_with_cases(cases, predictions)

    assert enriched[0]["question_text"] == "老师这个东风多利卡D6的仪表针脚有吗"
    assert enriched[0]["effective_query"] == "老师这个东风多利卡D6的仪表针脚有吗"
    assert enriched[0]["image_paths"] == ["/tmp/case_1.jpg"]
    assert enriched[0]["image_evidence_summary"] == "图片包含东风多利卡D6仪表盘及背面标签。"
    assert enriched[0]["case_snapshot"]["case_id"] == "case_1"


def test_write_excel_report_uses_gold_consistent_status(tmp_path: Path):
    cases = [
        {
            "case_id": "case_1",
            "input": {"question_text": "老师这个东风多利卡D6的仪表针脚有吗", "image_paths": []},
            "gold": {"answerable": False, "acceptable_doc_names": []},
        }
    ]
    predictions = [
        {
            "case_id": "case_1",
            "track": "production_flow",
            "answerable": True,
            "results": [{"rank": 1, "doc_name": "东风多利卡_仪表针脚定义"}],
            "results_scored": [{"rank": 1, "doc_name": "东风多利卡_仪表针脚定义"}],
            "results_full": [{"rank": 1, "doc_name": "东风多利卡_仪表针脚定义"}],
            "returned_result_count": 1,
            "full_result_count": 1,
            "matched_gold_names": [],
            "matched_result_doc_names": [],
            "runtime": {"response_type": "documents", "diagnostic_rank_source": "returned_results"},
            "error": None,
        }
    ]
    report = evaluate_predictions(cases, predictions)
    path = tmp_path / "report.xlsx"

    write_excel_report(
        path,
        config={"run_id": "run_test", "dataset_id": "demo_v0", "track": "production_flow", "top_k": 20},
        status={"status": "completed"},
        report=report,
        cases=cases,
        predictions=predictions,
        events=[],
    )

    workbook = load_workbook(path)
    sheet = workbook["Case明细"]
    assert sheet["B2"].value == "无资料误召回"
    assert sheet["C2"].value == "老师这个东风多利卡D6的仪表针脚有吗"
    assert sheet["F2"].value == "老师这个东风多利卡D6的仪表针脚有吗"
    assert sheet["H2"].value == "否"

