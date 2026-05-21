from __future__ import annotations

import json
from pathlib import Path


TRAIN_GOLD_PATH = (
    Path(__file__).resolve().parents[1]
    / "doc_search_bench"
    / "envs"
    / "doc_search"
    / "data"
    / "train"
    / "real_world_wecom_train.gold.json"
)

MOCK_MULTI_TARGET_CASE_IDS = {
    "real_train_0003",
    "real_train_0007",
    "real_train_0013",
}


def test_selected_train_cases_are_mocked_as_multi_target():
    payload = json.loads(TRAIN_GOLD_PATH.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in payload["cases"]}

    for case_id in MOCK_MULTI_TARGET_CASE_IDS:
        case = cases[case_id]
        assert case["target_match_mode"] == "any_of"
        assert len(case["accepted_titles"]) >= 2
        assert len(case["target_docs"]) >= 2
        assert case["preferred_title"] == case["target_doc"]["title"]
        assert case["preferred_title"] in case["accepted_titles"]
