from __future__ import annotations

import json
from pathlib import Path


DATA_ROOT = Path(__file__).resolve().parents[1] / "doc_search_bench" / "envs" / "doc_search" / "data"


def _load_gold_files() -> list[Path]:
    return sorted(DATA_ROOT.rglob("*.gold.json"))


def test_all_gold_cases_have_v2_target_docs_contract():
    gold_files = _load_gold_files()
    assert gold_files

    for path in gold_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload.get("cases", [])
        assert payload.get("case_count") == len(cases)
        for case in cases:
            target_docs = case.get("target_docs")
            assert isinstance(target_docs, list), path.as_posix()
            assert case.get("target_match_mode") in {"any_of", "all_of"}, path.as_posix()
            assert "target_doc" not in case, path.as_posix()
            assert "accepted_pages" not in case, path.as_posix()
            assert "accepted_page_ranges" not in case, path.as_posix()

            accepted_titles = case.get("accepted_titles", [])
            assert isinstance(accepted_titles, list), path.as_posix()
            accepted_title_set = {title for title in accepted_titles if isinstance(title, str) and title}

            if not target_docs:
                assert accepted_titles == [], path.as_posix()
                continue

            target_titles = [doc.get("title") for doc in target_docs if doc.get("title")]
            assert target_titles, path.as_posix()
            assert case.get("target_match_mode") == "any_of", path.as_posix()
            target_title_set = set(target_titles)
            assert accepted_title_set == target_title_set, path.as_posix()

            preferred_title = case.get("preferred_title")
            if preferred_title is not None:
                assert preferred_title in target_titles, path.as_posix()

            target_file_ids = {
                doc.get("file_id")
                for doc in target_docs
                if isinstance(doc.get("file_id"), str) and doc.get("file_id")
            }
            assert target_file_ids, path.as_posix()

            for doc in target_docs:
                assert "accepted_pages" in doc, path.as_posix()
                assert "accepted_page_ranges" in doc, path.as_posix()
                assert isinstance(doc.get("doc_path"), str) and doc.get("doc_path"), path.as_posix()
