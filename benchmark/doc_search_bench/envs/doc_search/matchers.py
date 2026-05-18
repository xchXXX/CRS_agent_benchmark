from __future__ import annotations

from typing import Any

from ...utils.text_norm import normalize_text


def candidate_strings(doc: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("doc_title", "doc_path"):
        raw = doc.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    return values


def matches_titles(doc: dict[str, Any], accepted_titles: list[str]) -> bool:
    candidates = [normalize_text(value) for value in candidate_strings(doc)]
    golds = [normalize_text(value) for value in accepted_titles if isinstance(value, str) and value.strip()]
    for candidate in candidates:
        for gold in golds:
            if candidate == gold or candidate in gold or gold in candidate:
                return True
    return False


def page_matches(expected_pages: list[int], expected_ranges: list[tuple[int, int]], candidate_page: int) -> bool:
    if candidate_page in expected_pages:
        return True
    for start, end in expected_ranges:
        if start <= candidate_page <= end:
            return True
    return False


def min_page_distance(
    expected_pages: list[int],
    expected_ranges: list[tuple[int, int]],
    predicted_pages: list[int],
) -> int | None:
    if not predicted_pages:
        return None
    distances: list[int] = []
    for page in predicted_pages:
        if expected_pages:
            distances.append(min(abs(page - expected) for expected in expected_pages))
        for start, end in expected_ranges:
            if start <= page <= end:
                distances.append(0)
            elif page < start:
                distances.append(start - page)
            else:
                distances.append(page - end)
    return min(distances) if distances else None
