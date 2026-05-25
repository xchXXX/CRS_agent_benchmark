from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.types import (
    TargetDocumentTruth,
    TaskCase,
    build_case_run_result,
    merge_suite_from_paths,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_base_fixture() -> dict:
    return {
        "suite_id": "suite_multi_target",
        "layer": "component",
        "source_files": ["source_demo.json"],
        "cases": [
            {
                "case_id": "multi_target_case_001",
                "input_modality": "text",
                "question_text": "帮我找一下相关资料",
                "question_images": [],
                "vehicle_info": None,
                "preprocess_strategy": "none",
                "benchmark_track": "chat_completions",
                "request_context": {},
                "user_id": "benchmark_user",
                "instruction": "请返回正确资料。",
                "initial_user_message": "帮我找一下相关资料",
            }
        ],
    }


def _build_base_task() -> TaskCase:
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

def test_merge_suite_from_paths_loads_v2_multi_target_truth_and_derives_titles(tmp_path: Path):
    fixture_path = tmp_path / "fixture.v2.json"
    gold_path = tmp_path / "gold.v2.json"
    _write_json(fixture_path, _build_base_fixture())
    _write_json(
        gold_path,
        {
            "acceptance_threshold": 1.0,
            "cases": [
                {
                    "case_id": "multi_target_case_001",
                    "target_match_mode": "all_of",
                    "target_docs": [
                        {
                            "file_id": "doc-a",
                            "title": "资料A",
                            "doc_path": "/docs/doc-a.pdf",
                        },
                        {
                            "file_id": "doc-b",
                            "title": "资料B",
                            "doc_path": "/docs/doc-b.pdf",
                        },
                    ],
                }
            ],
        },
    )

    suite = merge_suite_from_paths(split="dev", fixture_path=fixture_path, gold_path=gold_path)

    assert len(suite.cases) == 1
    task = suite.cases[0]
    assert hasattr(task, "target_docs")
    assert [doc.title for doc in task.target_docs] == ["资料A", "资料B"]
    assert task.accepted_titles == ["资料A", "资料B"]
    assert task.target_match_mode == "all_of"


def test_build_case_run_result_carries_multi_target_metadata():
    task = _build_base_task()
    object.__setattr__(
        task,
        "target_docs",
        [
            TargetDocumentTruth(file_id="doc-a", title="资料A", doc_path="/docs/doc-a.pdf"),
            TargetDocumentTruth(file_id="doc-b", title="资料B", doc_path="/docs/doc-b.pdf"),
        ],
    )
    object.__setattr__(task, "target_match_mode", "any_of")

    result = build_case_run_result(task, "run-multi-target")

    assert result.task_metadata.target_match_mode == "any_of"
    assert result.task_metadata.target_doc_count == 2
    assert result.task_metadata.target_doc_titles == ["资料A", "资料B"]
    assert result.task_metadata.target_doc_ids == ["doc-a", "doc-b"]
