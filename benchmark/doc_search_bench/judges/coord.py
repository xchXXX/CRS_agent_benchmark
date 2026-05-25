from __future__ import annotations

from collections import Counter
from typing import Any

from .locator import _extract_body_search_from_doc, _first_matching_doc_index, _prediction_docs
from .page import _matched_target_truths, judge_page


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return getattr(value, "__dict__", {}) or {}


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_box(raw_box: Any) -> list[float] | None:
    if isinstance(raw_box, dict):
        left = _safe_float(raw_box.get("x0"))
        top = _safe_float(raw_box.get("y0"))
        right = _safe_float(raw_box.get("x1"))
        bottom = _safe_float(raw_box.get("y1"))
        if None in {left, top, right, bottom}:
            left = _safe_float(raw_box.get("left"))
            top = _safe_float(raw_box.get("top"))
            right = _safe_float(raw_box.get("right"))
            bottom = _safe_float(raw_box.get("bottom"))
    elif isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
        left = _safe_float(raw_box[0])
        top = _safe_float(raw_box[1])
        right = _safe_float(raw_box[2])
        bottom = _safe_float(raw_box[3])
    else:
        return None
    if None in {left, top, right, bottom}:
        return None
    x0 = min(left, right)
    x1 = max(left, right)
    y0 = min(top, bottom)
    y1 = max(top, bottom)
    return [x0, y0, x1, y1]


def _normalize_norm_box(raw_box: Any) -> list[float] | None:
    box = _normalize_box(raw_box)
    if box is None:
        return None
    return [max(0.0, min(1.0, value)) for value in box]


def _rect_overlap(pred_box: list[float], gold_box: list[float]) -> bool:
    left = max(pred_box[0], gold_box[0])
    top = max(pred_box[1], gold_box[1])
    right = min(pred_box[2], gold_box[2])
    bottom = min(pred_box[3], gold_box[3])
    return right > left and bottom > top


def _target_docs(task) -> list[Any]:
    return list(getattr(task, "target_docs", None) or [])


def _matched_coord_targets(task, result) -> list[Any]:
    matched_targets = list(_matched_target_truths(task, result) or [])
    if matched_targets:
        return matched_targets
    target_docs = _target_docs(task)
    if len(target_docs) == 1:
        return target_docs
    return []


def _normalize_region_group(raw_group: Any) -> dict[str, Any] | None:
    group = _as_dict(raw_group)
    page_number = _safe_int(group.get("page_number"))
    if page_number is None:
        return None
    raw_boxes = group.get("boxes_norm")
    if not isinstance(raw_boxes, list):
        raw_boxes = []
    boxes_norm: list[list[float]] = []
    for item in raw_boxes:
        normalized = _normalize_norm_box(item)
        if normalized is not None:
            boxes_norm.append(normalized)
    return {
        "group_id": str(group.get("group_id") or "").strip() or f"page_{page_number}",
        "page_number": page_number,
        "label": str(group.get("label") or "").strip() or None,
        "boxes_norm": boxes_norm,
        "match_mode": str(group.get("match_mode") or "any_box").strip() or "any_box",
    }


def _resolve_region_groups(task, result) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for target in _matched_coord_targets(task, result):
        raw_groups = getattr(target, "accepted_region_groups", None)
        if not isinstance(raw_groups, list):
            continue
        for raw_group in raw_groups:
            group = _normalize_region_group(raw_group)
            if group is not None:
                groups.append(group)
    return groups


def _normalize_page_hits(body_search: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen_pages: set[int] = set()
    candidates: list[Any] = []
    best_hit = body_search.get("best_hit")
    if isinstance(best_hit, dict):
        candidates.append(best_hit)
    top_hits = body_search.get("top_hits")
    if isinstance(top_hits, list):
        candidates.extend(item for item in top_hits if isinstance(item, dict))

    for raw_hit in candidates:
        page_number = _safe_int(raw_hit.get("page_number"))
        if page_number is None or page_number in seen_pages:
            continue
        seen_pages.add(page_number)
        hits.append(_as_dict(raw_hit))
    return hits


def _hit_metadata(body_search: dict[str, Any], hit: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = [
        hit.get("metadata"),
        hit.get("page_metadata"),
        body_search.get("metadata"),
    ]
    page_metadata = body_search.get("page_metadata")
    page_number = _safe_int(hit.get("page_number"))
    if isinstance(page_metadata, dict) and page_number is not None:
        candidates.append(page_metadata.get(str(page_number)))
        candidates.append(page_metadata.get(page_number))
    for candidate in candidates:
        if isinstance(candidate, dict):
            width_px = _safe_float(candidate.get("width_px"))
            height_px = _safe_float(candidate.get("height_px"))
            if width_px and height_px:
                return {"width_px": width_px, "height_px": height_px}
    return None


def _hit_boxes_px(hit: dict[str, Any]) -> list[list[float]]:
    boxes: list[list[float]] = []
    for field_name in ("highlight_boxes_px", "initial_highlight_boxes_px", "boxes_px"):
        raw_boxes = hit.get(field_name)
        if not isinstance(raw_boxes, list):
            continue
        for item in raw_boxes:
            normalized = _normalize_box(item)
            if normalized is not None:
                boxes.append(normalized)
        if boxes:
            return boxes
    return boxes


def _boxes_norm_from_hit(body_search: dict[str, Any], hit: dict[str, Any]) -> tuple[list[list[float]], bool]:
    raw_boxes_norm = hit.get("boxes_norm")
    if isinstance(raw_boxes_norm, list):
        boxes_norm: list[list[float]] = []
        for raw_box in raw_boxes_norm:
            normalized = _normalize_norm_box(raw_box)
            if normalized is not None:
                boxes_norm.append(normalized)
        return boxes_norm, bool(boxes_norm)

    metadata = _hit_metadata(body_search, hit)
    if metadata is None:
        return [], False
    width_px = metadata["width_px"]
    height_px = metadata["height_px"]
    if width_px <= 0 or height_px <= 0:
        return [], False

    boxes_norm: list[list[float]] = []
    for box_px in _hit_boxes_px(hit):
        boxes_norm.append(
            [
                max(0.0, min(1.0, box_px[0] / width_px)),
                max(0.0, min(1.0, box_px[1] / height_px)),
                max(0.0, min(1.0, box_px[2] / width_px)),
                max(0.0, min(1.0, box_px[3] / height_px)),
            ]
        )
    return boxes_norm, True


def _coord_payload(task, result) -> tuple[dict[str, Any] | None, str | None]:
    prediction = getattr(result, "prediction", None)
    predicted_pages = getattr(prediction, "coord_predicted_page_numbers", None)
    predicted_boxes_norm = getattr(prediction, "coord_predicted_boxes_norm", None)
    has_predicted_pages = isinstance(predicted_pages, list) and bool(predicted_pages)
    has_predicted_boxes = isinstance(predicted_boxes_norm, list) and bool(predicted_boxes_norm)
    if has_predicted_pages or has_predicted_boxes:
        top_hits: list[dict[str, Any]] = []
        best_hit: dict[str, Any] | None = None
        page_to_boxes: dict[int, list[Any]] = {}
        for item in predicted_boxes_norm or []:
            page_number = _safe_int(getattr(item, "page_number", None))
            boxes = getattr(item, "boxes", None)
            if page_number is None or not isinstance(boxes, list):
                continue
            page_to_boxes[page_number] = list(boxes)
        seen_pages: set[int] = set()
        for page in predicted_pages or []:
            page_number = _safe_int(page)
            if page_number is None or page_number in seen_pages:
                continue
            seen_pages.add(page_number)
            hit: dict[str, Any] = {"page_number": page_number}
            boxes = page_to_boxes.get(page_number)
            if boxes:
                hit["boxes_norm"] = boxes
            top_hits.append(hit)
        best_page = _safe_int(getattr(prediction, "locator_best_page", None))
        if best_page is not None:
            best_hit = {"page_number": best_page}
            boxes = page_to_boxes.get(best_page)
            if boxes:
                best_hit["boxes_norm"] = boxes
            if best_page not in seen_pages:
                top_hits.insert(0, best_hit)
        payload: dict[str, Any] = {"top_hits": top_hits}
        if best_hit is not None:
            payload["best_hit"] = best_hit
        locator_status = getattr(prediction, "locator_status", None)
        if locator_status is not None:
            payload["status"] = locator_status
        return payload, "body_search"
    doc_index = _first_matching_doc_index(task, result)
    docs = _prediction_docs(result)
    if doc_index is None or doc_index >= len(docs):
        return None, None
    doc = docs[doc_index]
    locator_source = "body_search" if _extract_body_search_from_doc(doc) is not None else None
    return _extract_body_search_from_doc(doc), locator_source


def judge_coord(task, result) -> dict[str, Any]:
    region_groups = _resolve_region_groups(task, result)
    eligible = bool(region_groups)
    if not eligible:
        return {
            "eligible": False,
            "coord_gate_open": False,
            "doc_hit": bool(result.metrics.recall_hit),
            "page_hit": False,
            "coord_hit": None,
            "coord_status": None,
            "coord_hit_page_numbers": [],
            "coord_hit_group_ids": [],
            "coord_predicted_boxes_norm": [],
            "coord_failure_reason": None,
            "coord_blocking_failure": None,
            "coord_viewer_token_present": getattr(result.prediction, "locator_viewer_token_present", None),
            "coord_metadata_present": None,
            "warnings": [],
        }

    doc_hit = bool(result.metrics.recall_hit)
    if not doc_hit:
        return {
            "eligible": True,
            "coord_gate_open": False,
            "doc_hit": False,
            "page_hit": False,
            "coord_hit": False,
            "coord_status": None,
            "coord_hit_page_numbers": [],
            "coord_hit_group_ids": [],
            "coord_predicted_boxes_norm": [],
            "coord_failure_reason": "DOC_RECALL_MISS",
            "coord_blocking_failure": "DOC_RECALL_MISS",
            "coord_viewer_token_present": getattr(result.prediction, "locator_viewer_token_present", None),
            "coord_metadata_present": None,
            "warnings": ["DOC_RECALL_MISS"],
        }

    page_outcome = judge_page(task, result)
    page_hit = bool(page_outcome.get("page_hit_at_k"))
    if not page_hit:
        return {
            "eligible": True,
            "coord_gate_open": False,
            "doc_hit": True,
            "page_hit": False,
            "coord_hit": False,
            "coord_status": None,
            "coord_hit_page_numbers": [],
            "coord_hit_group_ids": [],
            "coord_predicted_boxes_norm": [],
            "coord_failure_reason": "PAGE_RECALL_MISS",
            "coord_blocking_failure": "PAGE_RECALL_MISS",
            "coord_viewer_token_present": getattr(result.prediction, "locator_viewer_token_present", None),
            "coord_metadata_present": None,
            "warnings": ["PAGE_RECALL_MISS"],
        }

    body_search, locator_source = _coord_payload(task, result)
    if not isinstance(body_search, dict):
        return {
            "eligible": True,
            "coord_gate_open": False,
            "doc_hit": True,
            "page_hit": True,
            "coord_hit": False,
            "coord_status": None,
            "coord_hit_page_numbers": [],
            "coord_hit_group_ids": [],
            "coord_predicted_boxes_norm": [],
            "coord_failure_reason": "BODY_SEARCH_MISSING",
            "coord_blocking_failure": "BODY_SEARCH_MISSING",
            "coord_viewer_token_present": getattr(result.prediction, "locator_viewer_token_present", None),
            "coord_metadata_present": None,
            "warnings": ["BODY_SEARCH_MISSING"],
        }

    region_groups_by_page: dict[int, list[dict[str, Any]]] = {}
    for group in region_groups:
        region_groups_by_page.setdefault(group["page_number"], []).append(group)

    predicted_hit_pages = _normalize_page_hits(body_search)
    hit_page_numbers = [page for page in (_safe_int(item.get("page_number")) for item in predicted_hit_pages) if page is not None]
    candidate_hits = [item for item in predicted_hit_pages if _safe_int(item.get("page_number")) in region_groups_by_page]

    coord_status = str(body_search.get("status") or "").strip() or locator_source
    viewer_token_present = bool(
        getattr(result.prediction, "locator_viewer_token_present", None)
        or body_search.get("viewer_token")
        or any(item.get("viewer_token") for item in predicted_hit_pages)
    )

    if not candidate_hits:
        return {
            "eligible": True,
            "coord_gate_open": False,
            "doc_hit": True,
            "page_hit": True,
            "coord_hit": False,
            "coord_status": coord_status,
            "coord_hit_page_numbers": [],
            "coord_hit_group_ids": [],
            "coord_predicted_boxes_norm": [],
            "coord_failure_reason": "PAGE_RECALL_MISS",
            "coord_blocking_failure": "PAGE_RECALL_MISS",
            "coord_viewer_token_present": viewer_token_present,
            "coord_metadata_present": None,
            "warnings": ["PAGE_RECALL_MISS"],
        }

    predicted_boxes_norm: list[dict[str, Any]] = []
    metadata_present = False
    hit_group_ids: list[str] = []
    hit_page_numbers_for_groups: list[int] = []
    box_present = False

    for hit in candidate_hits:
        page_number = _safe_int(hit.get("page_number"))
        if page_number is None:
            continue
        boxes_norm, hit_metadata_present = _boxes_norm_from_hit(body_search, hit)
        metadata_present = metadata_present or hit_metadata_present
        if boxes_norm:
            box_present = True
        predicted_boxes_norm.append(
            {
                "page_number": page_number,
                "boxes_norm": boxes_norm,
            }
        )
        if not boxes_norm:
            continue
        for group in region_groups_by_page.get(page_number, []):
            if group["match_mode"] != "any_box":
                continue
            group_hit = any(
                _rect_overlap(pred_box, gold_box)
                for pred_box in boxes_norm
                for gold_box in group["boxes_norm"]
            )
            if not group_hit:
                continue
            if page_number not in hit_page_numbers_for_groups:
                hit_page_numbers_for_groups.append(page_number)
            group_id = group["group_id"]
            if group_id not in hit_group_ids:
                hit_group_ids.append(group_id)

    if not metadata_present:
        failure_reason = "COORD_METADATA_MISSING"
    elif not box_present:
        failure_reason = "COORD_BOX_MISSING"
    elif not hit_group_ids:
        failure_reason = "COORD_REGION_MISS"
    else:
        failure_reason = None

    return {
        "eligible": True,
        "coord_gate_open": True,
        "doc_hit": True,
        "page_hit": True,
        "coord_hit": failure_reason is None,
        "coord_status": coord_status,
        "coord_hit_page_numbers": hit_page_numbers_for_groups,
        "coord_hit_group_ids": hit_group_ids,
        "coord_predicted_boxes_norm": predicted_boxes_norm,
        "coord_failure_reason": failure_reason,
        "coord_blocking_failure": failure_reason,
        "coord_viewer_token_present": viewer_token_present,
        "coord_metadata_present": metadata_present,
        "coord_compared_page_numbers": hit_page_numbers,
        "warnings": [failure_reason] if failure_reason else [],
    }


def aggregate_coord_reports(case_results, *, task_lookup: dict[tuple[str, str, str], Any] | None = None) -> dict[str, Any]:
    total_cases = len(case_results)
    outcomes = []
    for item in case_results:
        task = task_lookup.get((item.split, item.suite_id, item.case_id)) if task_lookup else None
        if task is None:
            task = getattr(item, "task_metadata", None)
        outcomes.append(judge_coord(task, item))

    eligible = [outcome for outcome in outcomes if outcome["eligible"]]
    doc_hit_outcomes = [outcome for outcome in eligible if outcome["doc_hit"]]
    page_hit_outcomes = [outcome for outcome in eligible if outcome["page_hit"]]
    failure_reason_counter: Counter[str] = Counter()
    for outcome in eligible:
        failure_reason = str(outcome.get("coord_failure_reason") or "").strip()
        if failure_reason:
            failure_reason_counter[failure_reason] += 1

    return {
        "count_basis": "attempt",
        "total_cases": total_cases,
        "eligible_cases": len(eligible),
        "doc_hit_cases": len(doc_hit_outcomes),
        "page_hit_cases": len(page_hit_outcomes),
        "coord_hit_rate": _rate(sum(1 for outcome in eligible if outcome["coord_hit"]), len(eligible)),
        "coord_hit_given_doc_hit_rate": _rate(
            sum(1 for outcome in doc_hit_outcomes if outcome["coord_hit"]),
            len(doc_hit_outcomes),
        ),
        "coord_hit_given_page_hit_rate": _rate(
            sum(1 for outcome in page_hit_outcomes if outcome["coord_hit"]),
            len(page_hit_outcomes),
        ),
        "coord_failure_reason_counts": dict(sorted(failure_reason_counter.items())),
    }
