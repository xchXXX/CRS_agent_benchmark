from __future__ import annotations

from typing import Any

from ..envs.doc_search.matchers import min_page_distance, page_matches


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def judge_page(task, result) -> dict[str, Any]:
    accepted_pages = task.accepted_pages
    accepted_ranges = task.accepted_page_ranges
    predicted_pages = result.prediction.predicted_pages or []
    page_goal_mode = getattr(task, "page_goal_mode", "disabled")
    eligible = page_goal_mode != "disabled" and bool(accepted_pages or accepted_ranges)

    if not eligible:
        return {
            "eligible": False,
            "gate_mode": page_goal_mode,
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
