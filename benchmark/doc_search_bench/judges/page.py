from __future__ import annotations

from typing import Any

from ..envs.doc_search.matchers import matches_titles, min_page_distance, page_matches


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _normalize_page_numbers(raw_value: object) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    if not isinstance(raw_value, list):
        return pages
    for item in raw_value:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page in seen:
            continue
        seen.add(page)
        pages.append(page)
    return pages


def _normalize_page_ranges(raw_value: object) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    if not isinstance(raw_value, list):
        return ranges
    for item in raw_value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            start = int(item[0])
            end = int(item[1])
        except (TypeError, ValueError):
            continue
        normalized = (start, end)
        if normalized in seen:
            continue
        seen.add(normalized)
        ranges.append(normalized)
    return ranges


def _truth_title(target: Any) -> str | None:
    title = getattr(target, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _truth_doc_path(target: Any) -> str | None:
    doc_path = getattr(target, "doc_path", None)
    if isinstance(doc_path, str) and doc_path.strip():
        return doc_path.strip()
    return None


def _target_truth_pages(target: Any) -> tuple[list[int], list[tuple[int, int]]]:
    pages = _normalize_page_numbers(getattr(target, "accepted_pages", None))
    ranges = _normalize_page_ranges(getattr(target, "accepted_page_ranges", None))
    return pages, ranges


def _merge_target_truths(targets: list[Any]) -> tuple[list[int], list[tuple[int, int]]]:
    merged_pages: list[int] = []
    merged_ranges: list[tuple[int, int]] = []
    seen_pages: set[int] = set()
    seen_ranges: set[tuple[int, int]] = set()
    for target in targets:
        pages, ranges = _target_truth_pages(target)
        for page in pages:
            if page in seen_pages:
                continue
            seen_pages.add(page)
            merged_pages.append(page)
        for page_range in ranges:
            if page_range in seen_ranges:
                continue
            seen_ranges.add(page_range)
            merged_ranges.append(page_range)
    return merged_pages, merged_ranges


def _runtime_matched_targets(result) -> list[str]:
    raw_value = getattr(result.metrics, "matched_targets", None)
    matched_targets: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw_value, list):
        return matched_targets
    for item in raw_value:
        if not isinstance(item, str):
            continue
        title = item.strip()
        if not title or title in seen:
            continue
        seen.add(title)
        matched_targets.append(title)
    return matched_targets


def _prediction_docs(result) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for item in getattr(result.prediction, "top_k_documents", None) or []:
        if isinstance(item, dict):
            docs.append(item)
            continue
        docs.append(getattr(item, "__dict__", {}))
    return docs


def _matches_target_document(doc: dict[str, Any], target: Any) -> bool:
    target_title = _truth_title(target)
    if target_title and matches_titles(doc, [target_title]):
        return True

    target_doc_path = _truth_doc_path(target)
    doc_path = doc.get("doc_path")
    if isinstance(target_doc_path, str) and isinstance(doc_path, str):
        normalized_target_path = target_doc_path.strip().lower()
        normalized_doc_path = doc_path.strip().lower()
        if normalized_target_path and normalized_doc_path:
            return (
                normalized_doc_path == normalized_target_path
                or normalized_doc_path.endswith(normalized_target_path)
                or normalized_target_path.endswith(normalized_doc_path)
            )
    return False


def _matched_target_truths(task, result) -> list[Any]:
    target_docs = list(getattr(task, "target_docs", None) or [])
    if not target_docs:
        return []

    matched_titles = set(_runtime_matched_targets(result))
    matched_targets: list[Any] = []
    if matched_titles:
        for target in target_docs:
            title = _truth_title(target)
            if title and title in matched_titles:
                matched_targets.append(target)
        if matched_targets:
            return matched_targets

    for doc in _prediction_docs(result):
        for target in target_docs:
            if target in matched_targets:
                continue
            if _matches_target_document(doc, target):
                matched_targets.append(target)
    return matched_targets


def _resolve_page_truth(task, result) -> tuple[list[int], list[tuple[int, int]], str]:
    matched_targets = _matched_target_truths(task, result)
    if matched_targets:
        pages, ranges = _merge_target_truths(matched_targets)
        if pages or ranges:
            return pages, ranges, "matched_target_docs"

    target_docs = list(getattr(task, "target_docs", None) or [])
    if len(target_docs) == 1:
        pages, ranges = _merge_target_truths(target_docs)
        if pages or ranges:
            return pages, ranges, "single_target_fallback"

    return [], [], "unresolved"


def judge_page(task, result) -> dict[str, Any]:
    accepted_pages, accepted_ranges, truth_source = _resolve_page_truth(task, result)
    predicted_pages = result.prediction.predicted_pages or []
    page_goal_mode = getattr(task, "page_goal_mode", "disabled")
    eligible = page_goal_mode != "disabled" and bool(accepted_pages or accepted_ranges)

    if not eligible:
        return {
            "eligible": False,
            "gate_mode": page_goal_mode,
            "truth_source": truth_source,
            "page_hit_at_1": None,
            "page_hit_at_k": None,
            "exact_page_hit": None,
            "page_range_overlap_hit": None,
            "min_page_distance": None,
            "warnings": [],
        }

    hit_at_1 = bool(predicted_pages) and page_matches(accepted_pages, accepted_ranges, predicted_pages[0])
    hit_at_k = any(page_matches(accepted_pages, accepted_ranges, page) for page in predicted_pages)
    exact_page_hit = any(page in accepted_pages for page in predicted_pages) if accepted_pages else False
    range_overlap_hit = any(
        any(start <= page <= end for start, end in accepted_ranges) for page in predicted_pages
    ) if accepted_ranges else False
    warnings: list[str] = []
    if not predicted_pages:
        if page_goal_mode == "shadow":
            warnings.append("PAGE_FEATURE_NOT_IMPLEMENTED")
        else:
            warnings.append("PAGE_MISS")
    elif not hit_at_k:
        if page_goal_mode == "shadow":
            warnings.append("PAGE_SHADOW_RANGE_MISS" if accepted_ranges else "PAGE_SHADOW_MISS")
        else:
            warnings.append("PAGE_RANGE_MISS" if accepted_ranges else "PAGE_MISS")

    return {
        "eligible": True,
        "gate_mode": page_goal_mode,
        "truth_source": truth_source,
        "page_hit_at_1": hit_at_1,
        "page_hit_at_k": hit_at_k,
        "exact_page_hit": exact_page_hit,
        "page_range_overlap_hit": range_overlap_hit,
        "min_page_distance": min_page_distance(accepted_pages, accepted_ranges, predicted_pages),
        "warnings": warnings,
    }


def aggregate_page_reports(case_results) -> dict[str, Any]:
    total_cases = len(case_results)
    disabled_cases = sum(1 for item in case_results if item.task_metadata.page_goal_mode == "disabled")
    shadow_cases = sum(1 for item in case_results if item.task_metadata.page_goal_mode == "shadow")
    required_cases = sum(1 for item in case_results if item.task_metadata.page_goal_mode == "required")
    eligible = [item for item in case_results if item.metrics.page_hit_at_k is not None]
    total = len(eligible)
    if total == 0:
        return {
            "total_cases": total_cases,
            "eligible_cases": 0,
            "disabled_cases": disabled_cases,
            "shadow_cases": shadow_cases,
            "required_cases": required_cases,
            "shadow_eligible_cases": 0,
            "required_eligible_cases": 0,
            "page_hit_at_1_rate": None,
            "page_hit_at_k_rate": None,
            "exact_page_hit_rate": None,
            "page_range_overlap_rate": None,
            "avg_min_page_distance": None,
        }

    distances = [item.metrics.min_page_distance for item in eligible if item.metrics.min_page_distance is not None]
    shadow_eligible_cases = sum(1 for item in eligible if item.task_metadata.page_goal_mode == "shadow")
    required_eligible_cases = sum(1 for item in eligible if item.task_metadata.page_goal_mode == "required")
    return {
        "total_cases": total_cases,
        "eligible_cases": total,
        "disabled_cases": disabled_cases,
        "shadow_cases": shadow_cases,
        "required_cases": required_cases,
        "shadow_eligible_cases": shadow_eligible_cases,
        "required_eligible_cases": required_eligible_cases,
        "page_hit_at_1_rate": _rate(sum(1 for item in eligible if item.metrics.page_hit_at_1), total),
        "page_hit_at_k_rate": _rate(sum(1 for item in eligible if item.metrics.page_hit_at_k), total),
        "exact_page_hit_rate": _rate(sum(1 for item in eligible if item.metrics.exact_page_hit), total),
        "page_range_overlap_rate": _rate(sum(1 for item in eligible if item.metrics.page_range_overlap_hit), total),
        "avg_min_page_distance": round(sum(distances) / len(distances), 6) if distances else None,
    }
