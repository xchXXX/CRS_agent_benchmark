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

OCR_GOLD_CASE_IDS = {
    "real_train_0002",
    "real_train_0004",
    "real_train_0011",
}


def test_selected_train_ocr_cases_have_page_and_coord_gold():
    payload = json.loads(TRAIN_GOLD_PATH.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in payload["cases"]}

    for case_id in OCR_GOLD_CASE_IDS:
        case = cases[case_id]
        target_docs = case.get("target_docs") or []
        assert len(target_docs) == 1

        target_doc = target_docs[0]
        accepted_pages = target_doc.get("accepted_pages") or []
        accepted_region_groups = target_doc.get("accepted_region_groups") or []

        assert accepted_pages, case_id
        assert accepted_region_groups, case_id
        assert case.get("page_goal_mode") == "shadow", case_id

        page_numbers_from_groups = {
            int(group["page_number"])
            for group in accepted_region_groups
            if group.get("page_number") is not None
        }
        assert page_numbers_from_groups == set(accepted_pages), case_id

        for group in accepted_region_groups:
            assert group.get("match_mode") == "any_box", case_id
            assert group.get("label"), case_id
            boxes = group.get("boxes_norm") or []
            assert boxes, case_id
            for box in boxes:
                assert len(box) == 4, case_id
                assert all(isinstance(value, (int, float)) for value in box), case_id
                assert all(0.0 <= float(value) <= 1.0 for value in box), case_id
