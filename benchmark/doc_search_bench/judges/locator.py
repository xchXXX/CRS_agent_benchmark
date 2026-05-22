from __future__ import annotations

from typing import Any

from ..envs.doc_search.matchers import min_page_distance, page_matches
from .page import _matched_target_truths


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


def _prediction_docs(result) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for item in getattr(result.prediction, "top_k_documents", None) or []:
        if isinstance(item, dict):
            docs.append(item)
            continue
        docs.append(getattr(item, "__dict__", {}))
    return docs


def _first_matching_doc_index(task, result) -> int | None:
    docs = _prediction_docs(result)
    if not docs:
        return None

    matched_targets = list(_matched_target_truths(task, result) or [])
    if matched_targets:
        for index, doc in enumerate(docs):
            doc_title = str(doc.get("doc_title") or "").strip()
            doc_path = str(doc.get("doc_path") or "").strip().lower()
            for target in matched_targets:
                target_title = str(getattr(target, "title", None) or "").strip()
                target_doc_path = str(getattr(target, "doc_path", None) or "").strip().lower()
                if target_title and doc_title == target_title:
                    return index
                if target_doc_path and doc_path and (
                    doc_path == target_doc_path
                    or doc_path.endswith(target_doc_path)
                    or target_doc_path.endswith(doc_path)
                ):
                    return index

    target_titles = [
        str(title).strip()
        for title in getattr(result.task_metadata, "target_doc_titles", None) or []
        if str(title).strip()
    ]
    if not target_titles:
        target_titles = [str(title).strip() for title in getattr(task, "accepted_titles", None) or [] if str(title).strip()]
    if not target_titles:
        return 0

    normalized_targets = {title.lower() for title in target_titles}
    exact_index: int | None = None
    fuzzy_index: int | None = None
    for index, doc in enumerate(docs):
        title = str(doc.get("doc_title") or "").strip().lower()
        doc_path = str(doc.get("doc_path") or "").strip().lower()
        if title in normalized_targets or doc_path in normalized_targets:
            exact_index = index
            break
        if fuzzy_index is None and any(
            target
            and (
                (title and (target in title or title in target))
                or (doc_path and (target in doc_path or doc_path in target))
            )
            for target in normalized_targets
        ):
            fuzzy_index = index
    if exact_index is not None:
        return exact_index
    if fuzzy_index is not None:
        return fuzzy_index
    return None


def _extract_body_search_from_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    body_search = doc.get("body_search")
    if isinstance(body_search, dict):
        return body_search
    return None


def _extract_locator_payload(task, result) -> tuple[dict[str, Any] | None, str | None]:
    prediction_payload = _locator_payload_from_result(result)
    if isinstance(prediction_payload, dict):
        return prediction_payload, _locator_source(result)
    doc_index = _first_matching_doc_index(task, result)
    docs = _prediction_docs(result)
    if doc_index is None or doc_index >= len(docs):
        return None, None
    return _extract_body_search_from_doc(docs[doc_index]), "body_search"


def _resolve_truth(task, result) -> tuple[list[int], list[tuple[int, int]]]:
    accepted_pages = _normalize_page_numbers(getattr(result.task_metadata, "accepted_pages", None))
    accepted_ranges = _normalize_page_ranges(getattr(result.task_metadata, "accepted_page_ranges", None))
    if accepted_pages or accepted_ranges:
        return accepted_pages, accepted_ranges
    return (
        _normalize_page_numbers(getattr(task, "accepted_pages", None)),
        _normalize_page_ranges(getattr(task, "accepted_page_ranges", None)),
    )


def _extract_top_pages(locator_payload: dict[str, Any]) -> list[int]:
    top_pages: list[int] = []
    seen: set[int] = set()
    top_hits = locator_payload.get("top_hits")
    if isinstance(top_hits, list):
        for item in top_hits:
            if not isinstance(item, dict):
                continue
            try:
                page = int(item.get("page_number"))
            except (TypeError, ValueError):
                continue
            if page in seen:
                continue
            seen.add(page)
            top_pages.append(page)
    best_hit = locator_payload.get("best_hit")
    if isinstance(best_hit, dict):
        try:
            best_page = int(best_hit.get("page_number"))
        except (TypeError, ValueError):
            best_page = None
        if best_page is not None and best_page not in seen:
            top_pages.insert(0, best_page)
    return top_pages


def _locator_payload_from_result(result) -> dict[str, Any] | None:
    prediction = getattr(result, "prediction", None)
    locator_top_pages = getattr(prediction, "locator_top_pages", None)
    locator_best_page = getattr(prediction, "locator_best_page", None)
    locator_status = getattr(prediction, "locator_status", None)
    if (
        (isinstance(locator_top_pages, list) and bool(locator_top_pages))
        or locator_best_page is not None
        or locator_status is not None
    ):
        top_hits: list[dict[str, Any]] = []
        seen: set[int] = set()
        if isinstance(locator_top_pages, list):
            for page in locator_top_pages:
                try:
                    normalized = int(page)
                except (TypeError, ValueError):
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                top_hits.append({"page_number": normalized})
        best_hit: dict[str, Any] | None = None
        try:
            normalized_best_page = int(locator_best_page) if locator_best_page is not None else None
        except (TypeError, ValueError):
            normalized_best_page = None
        if normalized_best_page is not None:
            best_hit = {"page_number": normalized_best_page}
            if normalized_best_page not in seen:
                top_hits.insert(0, {"page_number": normalized_best_page})
        payload: dict[str, Any] = {"top_hits": top_hits}
        if best_hit is not None:
            payload["best_hit"] = best_hit
        if locator_status is not None:
            payload["status"] = locator_status
        return payload

    locator_payload = getattr(getattr(result, "metrics", None), "locator", None)
    if isinstance(locator_payload, dict):
        return locator_payload
    body_search = getattr(getattr(result, "metrics", None), "body_search", None)
    if isinstance(body_search, dict):
        return body_search
    return None


def _locator_source(result) -> str | None:
    prediction = getattr(result, "prediction", None)
    locator_source = getattr(prediction, "locator_source", None)
    if isinstance(locator_source, str) and locator_source.strip():
        return locator_source.strip()
    metrics = getattr(result, "metrics", None)
    if isinstance(getattr(metrics, "locator", None), dict):
        return "locator"
    if isinstance(getattr(metrics, "body_search", None), dict):
        return "body_search"
    return None


def judge_locator(task, result) -> dict[str, Any]:
    accepted_pages, accepted_ranges = _resolve_truth(task, result)
    eligible = bool(accepted_pages or accepted_ranges)
    locator_payload, locator_source = _extract_locator_payload(task, result)
    if locator_payload is None:
        locator_payload = _locator_payload_from_result(result)
    if locator_source is None:
        locator_source = _locator_source(result)

    if not eligible:
        return {
            "eligible": False,
            "locator_source": locator_source,
            "locator_status": None,
            "locator_best_page": None,
            "locator_top_pages": [],
            "locator_hit_at_1": None,
            "locator_hit_at_k": None,
            "locator_exact_page_hit": None,
            "locator_range_overlap_hit": None,
            "locator_min_page_distance": None,
            "document_hit": bool(result.metrics.recall_hit),
            "document_hit_eligible": bool(result.metrics.recall_hit),
            "document_level_failure": None,
            "locator_blocking_failure": None,
            "warnings": [],
        }

    if not result.metrics.recall_hit:
        return {
            "eligible": True,
            "locator_source": locator_source,
            "locator_status": None,
            "locator_best_page": None,
            "locator_top_pages": [],
            "locator_hit_at_1": False,
            "locator_hit_at_k": False,
            "locator_exact_page_hit": False,
            "locator_range_overlap_hit": False,
            "locator_min_page_distance": None,
            "document_hit": False,
            "document_hit_eligible": False,
            "document_level_failure": "DOC_RECALL_MISS",
            "locator_blocking_failure": None,
            "warnings": [],
        }

    if not isinstance(locator_payload, dict):
        return {
            "eligible": True,
            "locator_source": locator_source,
            "locator_status": None,
            "locator_best_page": None,
            "locator_top_pages": [],
            "locator_hit_at_1": False,
            "locator_hit_at_k": False,
            "locator_exact_page_hit": False,
            "locator_range_overlap_hit": False,
            "locator_min_page_distance": None,
            "document_hit": True,
            "document_hit_eligible": True,
            "document_level_failure": "BODY_SEARCH_MISSING",
            "locator_blocking_failure": "BODY_SEARCH_MISSING",
            "warnings": ["BODY_SEARCH_MISSING"],
        }

    locator_status = str(locator_payload.get("status") or "").strip() or None
    top_pages = _extract_top_pages(locator_payload)
    best_page = top_pages[0] if top_pages else None
    hit_at_1 = best_page is not None and page_matches(accepted_pages, accepted_ranges, best_page)
    hit_at_k = any(page_matches(accepted_pages, accepted_ranges, page) for page in top_pages)
    exact_page_hit = any(page in accepted_pages for page in top_pages) if accepted_pages else False
    range_overlap_hit = (
        any(any(start <= page <= end for start, end in accepted_ranges) for page in top_pages)
        if accepted_ranges
        else False
    )
    warnings: list[str] = []
    document_level_failure: str | None = None
    if not top_pages:
        document_level_failure = "BODY_SEARCH_MISSING"
        warnings.append("BODY_SEARCH_MISSING")
    elif not hit_at_k:
        document_level_failure = "LOCATOR_PAGE_MISS"
        warnings.append("LOCATOR_PAGE_MISS")

    return {
        "eligible": True,
        "locator_source": locator_source,
        "locator_status": locator_status,
        "locator_best_page": best_page,
        "locator_top_pages": top_pages,
        "locator_hit_at_1": hit_at_1,
        "locator_hit_at_k": hit_at_k,
        "locator_exact_page_hit": exact_page_hit,
        "locator_range_overlap_hit": range_overlap_hit,
        "locator_min_page_distance": min_page_distance(accepted_pages, accepted_ranges, top_pages),
        "document_hit": True,
        "document_hit_eligible": True,
        "document_level_failure": document_level_failure,
        "locator_blocking_failure": document_level_failure,
        "warnings": warnings,
    }


def aggregate_locator_reports(case_results) -> dict[str, Any]:
    total_cases = len(case_results)
    outcomes = [judge_locator(None, item) for item in case_results]
    eligible = [outcome for outcome in outcomes if outcome["eligible"]]
    document_hit_eligible = [outcome for outcome in eligible if outcome["document_hit_eligible"]]
    if not eligible:
        return {
            "count_basis": "attempt",
            "total_cases": total_cases,
            "eligible_cases": 0,
            "document_hit_eligible_cases": 0,
            "locator_hit_at_1_rate": None,
            "locator_hit_at_k_rate": None,
            "locator_exact_page_hit_rate": None,
            "locator_range_overlap_hit_rate": None,
            "locator_hit_at_1_given_document_hit_rate": None,
            "locator_hit_at_k_given_document_hit_rate": None,
            "body_search_missing_count": 0,
            "locator_body_search_missing_count": 0,
            "locator_page_miss_count": 0,
            "locator_blocking_failure_counts": {},
        }

    conditional_outcomes = [outcome for outcome in eligible if outcome["document_hit_eligible"]]
    blocking_failure_counter: dict[str, int] = {}
    for outcome in eligible:
        failure = outcome.get("locator_blocking_failure")
        if not failure:
            continue
        blocking_failure_counter[failure] = blocking_failure_counter.get(failure, 0) + 1
    body_search_missing_count = sum(
        1 for outcome in eligible if outcome["document_level_failure"] == "BODY_SEARCH_MISSING"
    )
    locator_page_miss_count = sum(
        1 for outcome in eligible if outcome["document_level_failure"] == "LOCATOR_PAGE_MISS"
    )
    return {
        "count_basis": "attempt",
        "total_cases": total_cases,
        "eligible_cases": len(eligible),
        "document_hit_eligible_cases": len(conditional_outcomes),
        "locator_hit_at_1_rate": _rate(sum(1 for outcome in eligible if outcome["locator_hit_at_1"]), len(eligible)),
        "locator_hit_at_k_rate": _rate(sum(1 for outcome in eligible if outcome["locator_hit_at_k"]), len(eligible)),
        "locator_exact_page_hit_rate": _rate(
            sum(1 for outcome in eligible if outcome["locator_exact_page_hit"]),
            len(eligible),
        ),
        "locator_range_overlap_hit_rate": _rate(
            sum(1 for outcome in eligible if outcome["locator_range_overlap_hit"]),
            len(eligible),
        ),
        "locator_hit_at_1_given_document_hit_rate": _rate(
            sum(1 for outcome in conditional_outcomes if outcome["locator_hit_at_1"]),
            len(conditional_outcomes),
        ),
        "locator_hit_at_k_given_document_hit_rate": _rate(
            sum(1 for outcome in conditional_outcomes if outcome["locator_hit_at_k"]),
            len(conditional_outcomes),
        ),
        "body_search_missing_count": body_search_missing_count,
        "locator_body_search_missing_count": body_search_missing_count,
        "locator_page_miss_count": locator_page_miss_count,
        "locator_blocking_failure_counts": dict(sorted(blocking_failure_counter.items())),
    }
