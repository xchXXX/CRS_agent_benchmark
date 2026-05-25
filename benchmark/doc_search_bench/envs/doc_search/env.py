from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from ..base import BaseBenchmarkEnv
from ...judges.coord import judge_coord
from ...judges.contract import judge_contract
from ...judges.file import judge_file
from ...judges.locator import judge_locator
from ...judges.page import _matches_target_document, judge_page
from ...judges.trace import build_trace_analysis
from ...types import BenchmarkTurnRecord, CaseRunResult, PredictedDocument, RegionPageBoxes, TaskCase, build_case_run_result
from ...user import (
    AskUserDecisionContext,
    AskUserOption,
    generate_structured_user_decision,
    UserSimulationProviderError,
)
from ...utils.hashing import stable_hash
from .adapters import AdapterResult, DocSearchServiceAdapter
from .preprocessors import prepare_request_context


ROLLBACK_UNSUPPORTED_GAP = "当前新版 ask_user 主线暂不支持撤回上一轮，请重新发起查询。"
_INSTRUCTION_BLOCKLIST_MARKERS = (
    "room_id=",
    "chat_from=",
    "opening_message_id=",
    "answer_message_id=",
    "唯一答案来自",
)


@dataclass(frozen=True)
class ResolvedAskUserOption:
    key: str
    label: str
    description: str | None
    selection_payload: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_unique_page(pages: list[int], seen: set[int], raw_value: Any) -> None:
    if raw_value is None or isinstance(raw_value, bool):
        return
    try:
        page = int(raw_value)
    except (TypeError, ValueError):
        return
    if page in seen:
        return
    seen.add(page)
    pages.append(page)


def _has_present_value(raw_value: Any) -> bool:
    if isinstance(raw_value, str):
        return bool(raw_value.strip())
    return raw_value not in (None, False)


def extract_body_search(item: dict[str, Any]) -> dict[str, Any]:
    body_search = item.get("body_search")
    if isinstance(body_search, dict):
        return body_search
    return {}


def extract_body_search_page_numbers(body_search: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    best_hit = body_search.get("best_hit")
    if isinstance(best_hit, dict):
        _append_unique_page(pages, seen, best_hit.get("page_number"))
    top_hits = body_search.get("top_hits")
    if isinstance(top_hits, list):
        for hit in top_hits:
            if not isinstance(hit, dict):
                continue
            _append_unique_page(pages, seen, hit.get("page_number"))
    return pages


def _body_search_preview_present(body_search: dict[str, Any]) -> bool:
    if _has_present_value(body_search.get("preview_image_url")):
        return True
    if _has_present_value(body_search.get("preview_url")):
        return True
    if _has_present_value(body_search.get("preview")):
        return True
    best_hit = body_search.get("best_hit")
    if isinstance(best_hit, dict):
        if _has_present_value(best_hit.get("preview_image_url")):
            return True
        if _has_present_value(best_hit.get("preview_url")):
            return True
        if _has_present_value(best_hit.get("preview")):
            return True
    top_hits = body_search.get("top_hits")
    if isinstance(top_hits, list):
        for hit in top_hits:
            if not isinstance(hit, dict):
                continue
            if _has_present_value(hit.get("preview_image_url")):
                return True
            if _has_present_value(hit.get("preview_url")):
                return True
            if _has_present_value(hit.get("preview")):
                return True
    return False


def summarize_body_search(body_search: dict[str, Any]) -> dict[str, Any]:
    status = str(body_search.get("status") or "").strip() or None
    best_page: int | None = None
    top_pages: list[int] = []
    seen_pages: set[int] = set()
    viewer_token_present = _has_present_value(body_search.get("viewer_token"))
    preview_present = _body_search_preview_present(body_search)

    best_hit = body_search.get("best_hit")
    if isinstance(best_hit, dict):
        viewer_token_present = viewer_token_present or _has_present_value(best_hit.get("viewer_token"))
        if not preview_present:
            preview_present = _body_search_preview_present({"best_hit": best_hit})
        best_page = safe_int(best_hit.get("page_number"))
        if best_page is not None:
            seen_pages.add(best_page)
            top_pages.append(best_page)

    top_hits = body_search.get("top_hits")
    if isinstance(top_hits, list):
        for hit in top_hits:
            if not isinstance(hit, dict):
                continue
            viewer_token_present = viewer_token_present or _has_present_value(hit.get("viewer_token"))
            if not preview_present:
                preview_present = _has_present_value(hit.get("preview_image_url")) or _has_present_value(hit.get("preview_url")) or _has_present_value(hit.get("preview"))
            page = safe_int(hit.get("page_number"))
            if page is None or page in seen_pages:
                continue
            seen_pages.add(page)
            top_pages.append(page)

    if best_page is None and top_pages:
        best_page = top_pages[0]

    return {
        "body_search_status": status,
        "body_search_best_page": best_page,
        "body_search_top_pages": top_pages,
        "body_search_viewer_token_present": viewer_token_present,
        "body_search_preview_present": preview_present,
        "locator_status": status,
        "locator_best_page": best_page,
        "locator_top_pages": list(top_pages),
        "locator_viewer_token_present": viewer_token_present,
        "locator_preview_present": preview_present,
    }


def _coerce_region_box(raw_value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 4:
        return None
    try:
        return (
            float(raw_value[0]),
            float(raw_value[1]),
            float(raw_value[2]),
            float(raw_value[3]),
        )
    except (TypeError, ValueError):
        return None


def _extract_region_boxes(raw_value: Any) -> list[tuple[float, float, float, float]]:
    if not isinstance(raw_value, list):
        return []
    boxes: list[tuple[float, float, float, float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for item in raw_value:
        box = _coerce_region_box(item)
        if box is None or box in seen:
            continue
        seen.add(box)
        boxes.append(box)
    return boxes


def _first_present_text(*raw_values: Any) -> str | None:
    for raw_value in raw_values:
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
    return None


def _resolve_metadata_dimensions(raw_value: Any, *, page_number: int | None) -> tuple[float, float] | None:
    if not isinstance(raw_value, dict):
        return None
    width = safe_float(raw_value.get("width_px"))
    height = safe_float(raw_value.get("height_px"))
    if width is None or height is None:
        width = safe_float(raw_value.get("rendered_width_px"))
        height = safe_float(raw_value.get("rendered_height_px"))
    if width is not None and height is not None and width > 0 and height > 0:
        return width, height

    pages = raw_value.get("pages")
    if isinstance(pages, list):
        matched_page: dict[str, Any] | None = None
        fallback_page: dict[str, Any] | None = pages[0] if len(pages) == 1 and isinstance(pages[0], dict) else None
        for page in pages:
            if not isinstance(page, dict):
                continue
            candidate_page_number = safe_int(page.get("page_number"))
            candidate_page_index = safe_int(page.get("page_index"))
            if page_number is not None and candidate_page_number == page_number:
                matched_page = page
                break
            if page_number is not None and candidate_page_number is None and candidate_page_index == page_number - 1:
                matched_page = page
                break
        resolved = _resolve_metadata_dimensions(matched_page or fallback_page, page_number=page_number)
        if resolved is not None:
            return resolved
    return None


def _resolve_hit_metadata_dimensions(
    body_search: dict[str, Any],
    hit: dict[str, Any],
    *,
    page_number: int | None,
) -> tuple[float, float] | None:
    for candidate in (
        hit.get("metadata"),
        hit.get("page_metadata"),
        hit.get("viewer_metadata"),
        body_search.get("metadata"),
        body_search.get("viewer_metadata"),
    ):
        resolved = _resolve_metadata_dimensions(candidate, page_number=page_number)
        if resolved is not None:
            return resolved
    return None


def _normalize_region_box(
    box: tuple[float, float, float, float],
    *,
    width_px: float,
    height_px: float,
) -> tuple[float, float, float, float] | None:
    if width_px <= 0 or height_px <= 0:
        return None
    return (
        round(min(max(box[0] / width_px, 0.0), 1.0), 6),
        round(min(max(box[1] / height_px, 0.0), 1.0), 6),
        round(min(max(box[2] / width_px, 0.0), 1.0), 6),
        round(min(max(box[3] / height_px, 0.0), 1.0), 6),
    )


def summarize_coord_prediction(body_search: dict[str, Any]) -> dict[str, Any]:
    coord_predicted_page_numbers: list[int] = []
    seen_pages: set[int] = set()
    boxes_px_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    boxes_norm_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    seen_px_by_page: dict[int, set[tuple[float, float, float, float]]] = {}
    seen_norm_by_page: dict[int, set[tuple[float, float, float, float]]] = {}
    saw_coord_boxes = False
    metadata_present: bool | None = None
    viewer_token = _first_present_text(body_search.get("viewer_token"))

    hits: list[dict[str, Any]] = []
    best_hit = body_search.get("best_hit")
    if isinstance(best_hit, dict):
        hits.append(best_hit)
    top_hits = body_search.get("top_hits")
    if isinstance(top_hits, list):
        hits.extend(hit for hit in top_hits if isinstance(hit, dict))

    for hit in hits:
        if viewer_token is None:
            viewer_token = _first_present_text(hit.get("viewer_token"))
        page_number = safe_int(hit.get("page_number"))
        boxes_px = _extract_region_boxes(hit.get("highlight_boxes_px"))
        if page_number is None or not boxes_px:
            continue
        saw_coord_boxes = True
        if page_number not in seen_pages:
            seen_pages.add(page_number)
            coord_predicted_page_numbers.append(page_number)
        page_boxes_px = boxes_px_by_page.setdefault(page_number, [])
        seen_px = seen_px_by_page.setdefault(page_number, set())
        for box in boxes_px:
            if box in seen_px:
                continue
            seen_px.add(box)
            page_boxes_px.append(box)

        dimensions = _resolve_hit_metadata_dimensions(body_search, hit, page_number=page_number)
        if dimensions is None:
            if metadata_present is None:
                metadata_present = False
            continue
        metadata_present = True
        width_px, height_px = dimensions
        page_boxes_norm = boxes_norm_by_page.setdefault(page_number, [])
        seen_norm = seen_norm_by_page.setdefault(page_number, set())
        for box in boxes_px:
            normalized_box = _normalize_region_box(box, width_px=width_px, height_px=height_px)
            if normalized_box is None or normalized_box in seen_norm:
                continue
            seen_norm.add(normalized_box)
            page_boxes_norm.append(normalized_box)

    return {
        "coord_predicted_page_numbers": coord_predicted_page_numbers,
        "coord_predicted_boxes_px": [
            RegionPageBoxes(page_number=page_number, boxes=list(boxes_px_by_page.get(page_number, [])))
            for page_number in coord_predicted_page_numbers
            if boxes_px_by_page.get(page_number)
        ],
        "coord_predicted_boxes_norm": [
            RegionPageBoxes(page_number=page_number, boxes=list(boxes_norm_by_page.get(page_number, [])))
            for page_number in coord_predicted_page_numbers
            if boxes_norm_by_page.get(page_number)
        ],
        "coord_viewer_token": viewer_token,
        "coord_metadata_present": metadata_present if saw_coord_boxes else None,
        "coord_viewer_token_present": bool(viewer_token) if saw_coord_boxes or viewer_token is not None else None,
    }


def extract_page_numbers(item: dict[str, Any]) -> list[int]:
    body_search = extract_body_search(item)
    if body_search:
        return extract_body_search_page_numbers(body_search)

    pages: list[int] = []
    seen: set[int] = set()
    for key in ("page", "page_no", "page_num", "page_number", "page_numbers", "pages"):
        value = item.get(key)
        if isinstance(value, list):
            for member in value:
                _append_unique_page(pages, seen, member)
            continue
        _append_unique_page(pages, seen, value)
    return pages


def normalize_locator_prediction(
    raw_results: list[dict[str, Any]],
) -> dict[str, Any]:
    saw_body_search = False
    status_fallback: str | None = None
    best_page_fallback: int | None = None
    locator_status: str | None = None
    locator_best_page: int | None = None
    locator_top_pages: list[int] = []
    body_search_status: str | None = None
    body_search_best_page: int | None = None
    body_search_top_pages: list[int] = []
    seen_top_pages: set[int] = set()
    viewer_token_present = False
    preview_present = False
    coord_predicted_page_numbers: list[int] = []
    seen_coord_pages: set[int] = set()
    coord_boxes_px_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    coord_boxes_norm_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    coord_boxes_px_seen: dict[int, set[tuple[float, float, float, float]]] = {}
    coord_boxes_norm_seen: dict[int, set[tuple[float, float, float, float]]] = {}
    coord_viewer_token: str | None = None
    saw_coord_boxes = False
    coord_metadata_present: bool | None = None
    for item in raw_results:
        body_search = extract_body_search(item)
        if not body_search:
            continue
        saw_body_search = True

        summary = summarize_body_search(body_search)
        coord_summary = summarize_coord_prediction(body_search)
        status = summary.get("body_search_status")
        best_page = safe_int(summary.get("body_search_best_page"))
        top_pages = summary.get("body_search_top_pages") if isinstance(summary.get("body_search_top_pages"), list) else []

        if status_fallback is None and status is not None:
            status_fallback = status
        if status == "hit":
            locator_status = "hit"
        elif locator_status is None and status is not None:
            locator_status = status

        if body_search_status is None and status is not None:
            body_search_status = status
        elif body_search_status != "hit" and status == "hit":
            body_search_status = "hit"

        viewer_token_present = viewer_token_present or bool(summary.get("body_search_viewer_token_present"))
        preview_present = preview_present or bool(summary.get("body_search_preview_present"))
        if coord_viewer_token is None:
            coord_viewer_token = _first_present_text(coord_summary.get("coord_viewer_token"))

        if best_page_fallback is None and best_page is not None:
            best_page_fallback = best_page
        if locator_best_page is None and status == "hit" and best_page is not None:
            locator_best_page = best_page
        if body_search_best_page is None and best_page is not None:
            body_search_best_page = best_page
        elif body_search_best_page is None and isinstance(top_pages, list) and top_pages:
            body_search_best_page = safe_int(top_pages[0])

        if isinstance(top_pages, list):
            for page in top_pages:
                if isinstance(page, int) and page not in body_search_top_pages:
                    body_search_top_pages.append(page)

        for page in extract_body_search_page_numbers(body_search):
            if page in seen_top_pages:
                continue
            seen_top_pages.add(page)
            locator_top_pages.append(page)

        coord_pages = coord_summary.get("coord_predicted_page_numbers")
        if isinstance(coord_pages, list) and coord_pages:
            saw_coord_boxes = True
            for page in coord_pages:
                if not isinstance(page, int) or page in seen_coord_pages:
                    continue
                seen_coord_pages.add(page)
                coord_predicted_page_numbers.append(page)
        coord_metadata = coord_summary.get("coord_metadata_present")
        if coord_metadata is True:
            coord_metadata_present = True
        elif coord_metadata_present is None and coord_metadata is False:
            coord_metadata_present = False

        for field_name, target_map, target_seen in (
            ("coord_predicted_boxes_px", coord_boxes_px_by_page, coord_boxes_px_seen),
            ("coord_predicted_boxes_norm", coord_boxes_norm_by_page, coord_boxes_norm_seen),
        ):
            raw_page_boxes = coord_summary.get(field_name)
            if not isinstance(raw_page_boxes, list):
                continue
            for page_boxes in raw_page_boxes:
                page_number = safe_int(getattr(page_boxes, "page_number", None))
                boxes = getattr(page_boxes, "boxes", None)
                if page_number is None or not isinstance(boxes, list):
                    continue
                target_boxes = target_map.setdefault(page_number, [])
                seen_boxes = target_seen.setdefault(page_number, set())
                for box in boxes:
                    if not isinstance(box, tuple) or len(box) != 4 or box in seen_boxes:
                        continue
                    seen_boxes.add(box)
                    target_boxes.append(box)

    if locator_status is None:
        locator_status = status_fallback
    if locator_best_page is None:
        locator_best_page = best_page_fallback
    if body_search_status is None:
        body_search_status = status_fallback
    if body_search_best_page is None:
        body_search_best_page = best_page_fallback
    if not body_search_top_pages:
        body_search_top_pages = list(locator_top_pages)

    return {
        "locator_source": "body_search" if saw_body_search else None,
        "body_search_status": body_search_status,
        "body_search_best_page": body_search_best_page,
        "body_search_top_pages": body_search_top_pages,
        "body_search_viewer_token_present": viewer_token_present if saw_body_search else None,
        "body_search_preview_present": preview_present if saw_body_search else None,
        "locator_status": locator_status,
        "locator_best_page": locator_best_page,
        "locator_top_pages": locator_top_pages,
        "locator_viewer_token_present": viewer_token_present if saw_body_search else None,
        "locator_preview_present": preview_present if saw_body_search else None,
        "coord_predicted_page_numbers": coord_predicted_page_numbers,
        "coord_predicted_boxes_px": [
            RegionPageBoxes(page_number=page_number, boxes=list(coord_boxes_px_by_page.get(page_number, [])))
            for page_number in coord_predicted_page_numbers
            if coord_boxes_px_by_page.get(page_number)
        ],
        "coord_predicted_boxes_norm": [
            RegionPageBoxes(page_number=page_number, boxes=list(coord_boxes_norm_by_page.get(page_number, [])))
            for page_number in coord_predicted_page_numbers
            if coord_boxes_norm_by_page.get(page_number)
        ],
        "coord_viewer_token": coord_viewer_token,
        "coord_metadata_present": coord_metadata_present if saw_coord_boxes else None,
        "coord_viewer_token_present": bool(coord_viewer_token) if saw_body_search else None,
    }


def _filter_locator_source_results(
    raw_results: list[dict[str, Any]],
    docs: list[PredictedDocument],
    *,
    target_docs: list[Any] | None = None,
    matched_titles: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    if not raw_results or not docs:
        return raw_results, list(range(len(raw_results)))

    has_target_doc_filters = bool(target_docs)
    if target_docs:
        matched_indexes: list[int] = []
        for index, doc in enumerate(docs):
            doc_payload = getattr(doc, "__dict__", {})
            if any(_matches_target_document(doc_payload, target) for target in target_docs):
                matched_indexes.append(index)
        if matched_indexes:
            valid_indexes = [index for index in matched_indexes if 0 <= index < len(raw_results)]
            return [raw_results[index] for index in valid_indexes], valid_indexes

    normalized_targets = {str(title).strip().lower() for title in (matched_titles or []) if str(title).strip()}
    has_title_filters = bool(normalized_targets)
    if not normalized_targets:
        if has_target_doc_filters:
            return [], []
        return raw_results, list(range(len(raw_results)))

    exact_indexes: list[int] = []
    fuzzy_indexes: list[int] = []
    for index, doc in enumerate(docs):
        title = str(getattr(doc, "doc_title", "") or "").strip().lower()
        doc_path = str(getattr(doc, "doc_path", "") or "").strip().lower()
        if title in normalized_targets or doc_path in normalized_targets:
            exact_indexes.append(index)
            continue
        if any(
            target
            and (
                (title and (target in title or title in target))
                or (doc_path and (target in doc_path or doc_path in target))
            )
            for target in normalized_targets
        ):
            fuzzy_indexes.append(index)

    selected_indexes = exact_indexes or fuzzy_indexes
    if selected_indexes:
        valid_indexes = [index for index in selected_indexes if 0 <= index < len(raw_results)]
        return [raw_results[index] for index in valid_indexes], valid_indexes
    if has_target_doc_filters or has_title_filters:
        return [], []
    return raw_results, list(range(len(raw_results)))


def clip_text(value: str | None, *, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def summarize_option_labels(options: list[Any], *, limit: int = 3) -> str | None:
    labels: list[str] = []
    for option in options[:limit]:
        label = ""
        if isinstance(option, ResolvedAskUserOption):
            label = option.label or option.key
        elif isinstance(option, dict):
            raw_label = option.get("label") or option.get("key")
            if raw_label is not None:
                label = str(raw_label).strip()
        if label:
            labels.append(label)
    if not labels:
        return None
    suffix = " 等" if len(options) > limit else ""
    return f"{'、'.join(labels)}{suffix}"


def sanitize_instruction_text(instruction: str) -> str:
    normalized = str(instruction or "").replace("\r\n", "\n")
    kept_lines: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if kept_lines and kept_lines[-1] != "":
                kept_lines.append("")
            continue
        if any(marker in line for marker in _INSTRUCTION_BLOCKLIST_MARKERS):
            continue
        kept_lines.append(line)
    while kept_lines and kept_lines[-1] == "":
        kept_lines.pop()
    return "\n".join(kept_lines).strip()


def build_visible_card_summary(ask_user_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(ask_user_payload, dict):
        return None

    context = ask_user_payload.get("context")
    if not isinstance(context, dict):
        return None

    parts: list[str] = []
    message_text = clip_text(context.get("message"), limit=160)
    if message_text:
        parts.append(message_text)

    top_result = context.get("top_result")
    if isinstance(top_result, dict):
        top_bits = [
            clip_text(top_result.get("title"), limit=80),
            clip_text(top_result.get("brand"), limit=40),
            clip_text(top_result.get("series"), limit=40),
            clip_text(top_result.get("model"), limit=40),
        ]
        top_values = [bit for bit in top_bits if bit]
        if top_values:
            parts.append(f"候选摘要：{' / '.join(top_values)}")

    existence_info = context.get("existence_info")
    if isinstance(existence_info, dict):
        existence_message = clip_text(existence_info.get("message"), limit=120)
        if existence_message:
            parts.append(f"提示：{existence_message}")

    deduped: list[str] = []
    for item in parts:
        if item and item not in deduped:
            deduped.append(item)
    return "\n".join(deduped).strip() or None


def build_decision_ask_user_context(ask_user_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ask_user_payload, dict):
        return {}

    context = ask_user_payload.get("context")
    normalized_context = dict(context) if isinstance(context, dict) else {}
    visible_card_summary = build_visible_card_summary(ask_user_payload)
    if visible_card_summary:
        normalized_context["visible_card_summary"] = visible_card_summary
    return normalized_context


def summarize_document_titles(docs: list[PredictedDocument], *, limit: int = 3) -> str | None:
    titles = [doc.doc_title.strip() for doc in docs[:limit] if doc.doc_title.strip()]
    if not titles:
        return None
    suffix = " 等" if len(docs) > limit else ""
    return f"{'、'.join(titles)}{suffix}"


def summarize_int_list(values: list[int], *, limit: int = 5) -> str | None:
    if not values:
        return None
    preview = values[:limit]
    suffix = " 等" if len(values) > limit else ""
    return f"{'、'.join(str(value) for value in preview)}{suffix}"


def summarize_codes(values: list[str], *, limit: int = 6) -> str | None:
    if not values:
        return None
    preview = [value for value in values[:limit] if value]
    if not preview:
        return None
    suffix = " 等" if len(values) > limit else ""
    return f"{'、'.join(preview)}{suffix}"


def summarize_evidence(evidence: dict[str, Any] | None) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    detail: list[str] = []
    supports = evidence.get("supports")
    if isinstance(supports, list):
        rendered = "、".join(str(item).strip() for item in supports if str(item).strip())
        if rendered:
            detail.append(f"supports={rendered}")
    conflicts = evidence.get("conflicts")
    if isinstance(conflicts, list):
        rendered = "、".join(str(item).strip() for item in conflicts if str(item).strip())
        if rendered:
            detail.append(f"conflicts={rendered}")
    return detail


def normalize_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_task_target_titles(task: TaskCase) -> list[str]:
    titles: list[str] = []
    for target in getattr(task, "target_docs", []) or []:
        raw = getattr(target, "title", None)
        if isinstance(raw, str) and raw.strip():
            titles.append(raw.strip())
    if titles:
        return normalize_text_list(titles)
    return normalize_text_list(list(getattr(task, "accepted_titles", []) or []))


def build_multi_target_trace_payload(task: TaskCase, result: CaseRunResult, file_outcome: dict[str, Any]) -> dict[str, Any]:
    task_metadata = result.task_metadata
    target_titles = normalize_text_list(list(getattr(task_metadata, "target_doc_titles", []) or []))
    if not target_titles:
        target_titles = resolve_task_target_titles(task)

    target_doc_ids = normalize_text_list(list(getattr(task_metadata, "target_doc_ids", []) or []))
    matched_targets = normalize_text_list(file_outcome.get("matched_targets"))
    missed_targets = normalize_text_list(file_outcome.get("missed_targets"))
    target_doc_count = safe_int(file_outcome.get("target_doc_count"))
    if target_doc_count is None:
        target_doc_count = safe_int(getattr(task_metadata, "target_doc_count", None))
    if target_doc_count is None:
        target_doc_count = len(target_titles)
    matched_target_count = safe_int(file_outcome.get("matched_target_count"))
    if matched_target_count is None:
        matched_target_count = len(matched_targets)
    target_coverage_rate = safe_float(file_outcome.get("target_coverage_rate"))
    if target_coverage_rate is None:
        target_coverage_rate = 0.0 if target_doc_count <= 0 else round(matched_target_count / target_doc_count, 6)
    all_targets_hit = file_outcome.get("all_targets_hit")
    if not isinstance(all_targets_hit, bool):
        all_targets_hit = bool(target_doc_count > 0 and matched_target_count == target_doc_count)
    best_target_rank = safe_int(file_outcome.get("best_target_rank"))
    target_match_mode = str(
        file_outcome.get("target_match_mode") or getattr(task_metadata, "target_match_mode", None) or "any_of"
    ).strip() or "any_of"

    return {
        "trace_kind": "file_judge_multi_target",
        "target_match_mode": target_match_mode,
        "target_doc_count": target_doc_count,
        "target_doc_ids": target_doc_ids,
        "target_doc_titles": target_titles,
        "matched_targets": matched_targets,
        "missed_targets": missed_targets,
        "matched_target_count": matched_target_count,
        "target_coverage_rate": round(target_coverage_rate, 6),
        "all_targets_hit": all_targets_hit,
        "best_target_rank": best_target_rank,
        "recall_hit": bool(file_outcome.get("recall_hit")),
        "hit_at_1": bool(file_outcome.get("hit_at_1")),
        "hit_at_3": bool(file_outcome.get("hit_at_3")),
        "mrr": round(float(file_outcome.get("mrr") or 0.0), 6),
    }


def attach_multi_target_runtime_fields(task: TaskCase, result: CaseRunResult, file_outcome: dict[str, Any]) -> dict[str, Any]:
    payload = build_multi_target_trace_payload(task, result, file_outcome)

    result.task_metadata.target_match_mode = str(payload["target_match_mode"])
    result.task_metadata.target_doc_count = int(payload["target_doc_count"])
    if payload["target_doc_ids"]:
        result.task_metadata.target_doc_ids = list(payload["target_doc_ids"])
    if payload["target_doc_titles"]:
        result.task_metadata.target_doc_titles = list(payload["target_doc_titles"])
    if not result.task_metadata.accepted_titles and payload["target_doc_titles"]:
        result.task_metadata.accepted_titles = list(payload["target_doc_titles"])

    for key in (
        "target_match_mode",
        "target_doc_count",
        "matched_targets",
        "missed_targets",
        "matched_target_count",
        "target_coverage_rate",
        "all_targets_hit",
        "best_target_rank",
    ):
        setattr(result.metrics, key, payload[key])
        setattr(result.analysis, key, payload[key])
    return payload


def upsert_multi_target_trace(result: CaseRunResult, payload: dict[str, Any]) -> None:
    trace = list(result.analysis.decision_trace or [])
    filtered = [
        item
        for item in trace
        if not (isinstance(item, dict) and str(item.get("trace_kind") or "") == "file_judge_multi_target")
    ]
    filtered.append(dict(payload))
    result.analysis.decision_trace = filtered


def _first_non_empty_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_doc_title(item: dict[str, Any]) -> str:
    return _first_non_empty_text(
        item,
        (
            "filename",
            "title",
            "name",
            "file_name",
            "file_id",
        ),
    )


def resolve_doc_path(item: dict[str, Any]) -> str:
    # External ggzj results often omit hierarchy/path fields. Fall back to a
    # stable document identifier so benchmark contract validation can still
    # treat the returned item as an addressable document record.
    return _first_non_empty_text(
        item,
        (
            "hierarchy_full",
            "path",
            "physical_path",
            "file_path",
            "doc_path",
            "file_id",
            "id",
            "filename",
            "title",
        ),
    )


def _build_predicted_document(item: dict[str, Any], *, rank: int) -> PredictedDocument:
    body_search = extract_body_search(item)
    summary = summarize_body_search(body_search) if body_search else {}
    coord_summary = summarize_coord_prediction(body_search) if body_search else {}
    return PredictedDocument(
        rank=rank,
        doc_title=resolve_doc_title(item),
        doc_path=resolve_doc_path(item),
        score=float(item["score"]) if isinstance(item.get("score"), (int, float)) else None,
        page_numbers=extract_page_numbers(item),
        body_search=dict(body_search),
        body_search_status=summary.get("body_search_status") if body_search else None,
        body_search_best_page=safe_int(summary.get("body_search_best_page")) if body_search else None,
        body_search_top_pages=[
            page for page in summary.get("body_search_top_pages", []) if isinstance(page, int)
        ] if body_search else [],
        body_search_viewer_token_present=(
            bool(summary.get("body_search_viewer_token_present")) if body_search else None
        ),
        body_search_preview_present=(
            bool(summary.get("body_search_preview_present")) if body_search else None
        ),
        locator_status=summary.get("locator_status") if body_search else None,
        locator_best_page=safe_int(summary.get("locator_best_page")) if body_search else None,
        locator_top_pages=[
            page for page in summary.get("locator_top_pages", []) if isinstance(page, int)
        ] if body_search else [],
        locator_viewer_token_present=(
            bool(summary.get("locator_viewer_token_present")) if body_search else None
        ),
        locator_preview_present=(
            bool(summary.get("locator_preview_present")) if body_search else None
        ),
        coord_predicted_page_numbers=[
            page
            for page in coord_summary.get("coord_predicted_page_numbers", [])
            if isinstance(page, int)
        ] if body_search else [],
        coord_predicted_boxes_px=(
            list(coord_summary.get("coord_predicted_boxes_px", []))
            if isinstance(coord_summary.get("coord_predicted_boxes_px"), list)
            else []
        ),
        coord_predicted_boxes_norm=(
            list(coord_summary.get("coord_predicted_boxes_norm", []))
            if isinstance(coord_summary.get("coord_predicted_boxes_norm"), list)
            else []
        ),
        coord_viewer_token=(
            str(coord_summary.get("coord_viewer_token"))
            if isinstance(coord_summary.get("coord_viewer_token"), str) and str(coord_summary.get("coord_viewer_token")).strip()
            else None
        ),
        coord_metadata_present=(
            bool(coord_summary.get("coord_metadata_present"))
            if coord_summary.get("coord_metadata_present") is not None
            else None
        ),
    )


def normalize_documents(
    track: str,
    body: dict[str, Any],
    *,
    target_docs: list[Any] | None = None,
    matched_titles: list[str] | None = None,
) -> tuple[str, list[PredictedDocument], list[int], float | None, dict[str, Any]]:
    docs: list[PredictedDocument] = []
    predicted_pages: list[int] = []
    page_confidence: float | None = None
    locator_summary: dict[str, Any] = {
        "locator_source": None,
        "locator_status": None,
        "locator_best_page": None,
        "locator_top_pages": [],
        "locator_viewer_token_present": None,
        "locator_preview_present": None,
        "coord_predicted_page_numbers": [],
        "coord_predicted_boxes_px": [],
        "coord_predicted_boxes_norm": [],
        "coord_viewer_token": None,
        "coord_metadata_present": None,
        "coord_viewer_token_present": None,
    }

    if track == "search_api":
        raw_results = body.get("results") or []
        response_type = "documents" if raw_results else "message"
        for idx, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            doc = _build_predicted_document(item, rank=idx)
            docs.append(doc)
            predicted_pages.extend(doc.page_numbers)
        locator_results = [item for item in raw_results if isinstance(item, dict)]
        filtered_locator_results, matched_indexes = _filter_locator_source_results(
            locator_results,
            docs,
            target_docs=target_docs,
            matched_titles=matched_titles,
        )
        if target_docs or matched_titles:
            predicted_pages = []
            for index in matched_indexes:
                if 0 <= index < len(docs):
                    predicted_pages.extend(docs[index].page_numbers)
        locator_summary = normalize_locator_prediction(filtered_locator_results)
    else:
        response_type = str(body.get("type") or "")
        content = body.get("content") if isinstance(body.get("content"), dict) else {}
        raw_results = content.get("results") or []
        if not response_type:
            response_type = "documents" if raw_results else "message"
        for idx, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            doc = _build_predicted_document(item, rank=idx)
            docs.append(doc)
            predicted_pages.extend(doc.page_numbers)
        if isinstance(content.get("page_confidence"), (int, float)):
            page_confidence = float(content["page_confidence"])
        elif isinstance(body.get("page_confidence"), (int, float)):
            page_confidence = float(body["page_confidence"])
        locator_results = [item for item in raw_results if isinstance(item, dict)]
        filtered_locator_results, matched_indexes = _filter_locator_source_results(
            locator_results,
            docs,
            target_docs=target_docs,
            matched_titles=matched_titles,
        )
        if target_docs or matched_titles:
            predicted_pages = []
            for index in matched_indexes:
                if 0 <= index < len(docs):
                    predicted_pages.extend(docs[index].page_numbers)
        locator_summary = normalize_locator_prediction(filtered_locator_results)

    deduped_pages: list[int] = []
    seen = set()
    for page in predicted_pages:
        if page not in seen:
            seen.add(page)
            deduped_pages.append(page)
    if locator_summary["locator_source"] == "body_search":
        deduped_pages = list(locator_summary["locator_top_pages"])
    return response_type, docs, deduped_pages, page_confidence, locator_summary


def apply_locator_prediction(result: CaseRunResult, locator_summary: dict[str, Any]) -> None:
    result.prediction.locator_source = (
        str(locator_summary.get("locator_source"))
        if locator_summary.get("locator_source") is not None
        else None
    )
    result.prediction.body_search_status = (
        str(locator_summary.get("body_search_status"))
        if locator_summary.get("body_search_status") is not None
        else None
    )
    result.prediction.body_search_best_page = safe_int(locator_summary.get("body_search_best_page"))
    result.prediction.body_search_top_pages = [
        page
        for page in locator_summary.get("body_search_top_pages", [])
        if isinstance(page, int)
    ] if isinstance(locator_summary.get("body_search_top_pages"), list) else []
    result.prediction.body_search_viewer_token_present = (
        bool(locator_summary.get("body_search_viewer_token_present"))
        if locator_summary.get("body_search_viewer_token_present") is not None
        else None
    )
    result.prediction.body_search_preview_present = (
        bool(locator_summary.get("body_search_preview_present"))
        if locator_summary.get("body_search_preview_present") is not None
        else None
    )
    result.prediction.locator_status = (
        str(locator_summary.get("locator_status"))
        if locator_summary.get("locator_status") is not None
        else None
    )
    result.prediction.locator_best_page = safe_int(locator_summary.get("locator_best_page"))
    result.prediction.locator_top_pages = [
        page
        for page in locator_summary.get("locator_top_pages", [])
        if isinstance(page, int)
    ] if isinstance(locator_summary.get("locator_top_pages"), list) else []
    result.prediction.locator_viewer_token_present = (
        bool(locator_summary.get("locator_viewer_token_present"))
        if locator_summary.get("locator_viewer_token_present") is not None
        else None
    )
    result.prediction.locator_preview_present = (
        bool(locator_summary.get("locator_preview_present"))
        if locator_summary.get("locator_preview_present") is not None
        else None
    )
    result.prediction.coord_predicted_page_numbers = [
        page
        for page in locator_summary.get("coord_predicted_page_numbers", [])
        if isinstance(page, int)
    ] if isinstance(locator_summary.get("coord_predicted_page_numbers"), list) else []
    result.prediction.coord_predicted_boxes_px = (
        list(locator_summary.get("coord_predicted_boxes_px", []))
        if isinstance(locator_summary.get("coord_predicted_boxes_px"), list)
        else []
    )
    result.prediction.coord_predicted_boxes_norm = (
        list(locator_summary.get("coord_predicted_boxes_norm", []))
        if isinstance(locator_summary.get("coord_predicted_boxes_norm"), list)
        else []
    )
    result.prediction.coord_viewer_token = (
        str(locator_summary.get("coord_viewer_token"))
        if isinstance(locator_summary.get("coord_viewer_token"), str)
        and str(locator_summary.get("coord_viewer_token")).strip()
        else None
    )
    result.prediction.coord_metadata_present = (
        bool(locator_summary.get("coord_metadata_present"))
        if locator_summary.get("coord_metadata_present") is not None
        else None
    )
    result.metrics.coord_metadata_present = result.prediction.coord_metadata_present
    result.metrics.coord_viewer_token_present = (
        bool(result.prediction.coord_viewer_token) if result.prediction.coord_viewer_token is not None else None
    )


def extract_ask_user_payload(body: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    ask_user = body.get("ask_user")
    if isinstance(ask_user, dict):
        return ask_user
    content = body.get("content")
    if str(body.get("type") or "") == "ask_user" and isinstance(content, dict):
        return content
    return {}


def normalize_ask_user_options(body: dict[str, Any] | None) -> list[ResolvedAskUserOption]:
    if not isinstance(body, dict):
        return []

    ask_user = extract_ask_user_payload(body)
    option_sources: list[list[dict[str, Any]]] = []
    ask_user_options = ask_user.get("options")
    if isinstance(ask_user_options, list):
        option_sources.append([item for item in ask_user_options if isinstance(item, dict)])
    clarify_options = body.get("clarify_options")
    if isinstance(clarify_options, list):
        option_sources.append([item for item in clarify_options if isinstance(item, dict)])

    normalized: list[ResolvedAskUserOption] = []
    index_by_identity: dict[str, int] = {}
    for source in option_sources:
        for item in source:
            key = str(item.get("key") or "").strip()
            label = str(item.get("label") or "").strip()
            description = str(item.get("description")).strip() if isinstance(item.get("description"), str) else None
            selection_payload = item.get("selection_payload")
            if not isinstance(selection_payload, dict):
                selection_payload = {}
            identity = key or label
            if not identity:
                continue
            existing_index = index_by_identity.get(identity)
            if existing_index is None:
                index_by_identity[identity] = len(normalized)
                normalized.append(
                    ResolvedAskUserOption(
                        key=key,
                        label=label,
                        description=description,
                        selection_payload=dict(selection_payload),
                    )
                )
                continue

            existing = normalized[existing_index]
            merged_key = existing.key or key
            merged_label = existing.label or label
            merged_description = existing.description or description
            merged_payload = existing.selection_payload or dict(selection_payload)
            normalized[existing_index] = ResolvedAskUserOption(
                key=merged_key,
                label=merged_label,
                description=merged_description,
                selection_payload=dict(merged_payload),
            )
    return normalized


def summarize_response_content(
    body: dict[str, Any] | None,
    response_type: str,
    error_message: str | None = None,
) -> str | None:
    if error_message:
        return error_message
    if not isinstance(body, dict):
        return None

    ask_user = extract_ask_user_payload(body)
    if response_type == "ask_user":
        question = ask_user.get("question")
        return str(question).strip() if isinstance(question, str) and str(question).strip() else None

    content = body.get("content")
    if response_type == "documents":
        if isinstance(content, dict):
            summary = content.get("summary") or content.get("message")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
        if isinstance(content, str) and content.strip():
            return content.strip()
        return None

    if response_type == "message":
        if isinstance(content, dict):
            message = content.get("message") or content.get("summary")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(content, str) and content.strip():
            return content.strip()
        return None

    if response_type == "error":
        for key in ("message", "detail", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


class DocSearchBenchmarkEnv(BaseBenchmarkEnv):
    def __init__(self, *, config, benchmark_root: Path, run_id: str) -> None:
        super().__init__(config=config, benchmark_root=benchmark_root, run_id=run_id)
        self.adapter = DocSearchServiceAdapter(
            base_url=config.base_url,
            app_token=config.app_token,
            timeout_ms=config.timeout_ms,
            top_k=config.top_k,
            request_mode=config.request_mode,
        )

    def write_raw_response(
        self,
        task: TaskCase,
        *,
        attempt_index: int,
        turn_index: int,
        request_kind: str,
        body: dict[str, Any] | None,
    ) -> str | None:
        if body is None:
            return None
        path = self.raw_root / (
            f"{task.case_id}.attempt_{attempt_index}.turn_{turn_index}.{request_kind}.raw.json"
        )
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    @staticmethod
    def refresh_workflow_counters(result: CaseRunResult) -> None:
        result.workflow.conversation_turn_count = len(result.workflow.turns)
        result.workflow.ask_user_rounds = sum(
            1 for turn in result.workflow.turns if turn.response_type == "ask_user"
        )

    @staticmethod
    def append_workflow_message(
        result: CaseRunResult,
        *,
        role: str,
        content: str,
        message_type: str,
        turn_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not content.strip():
            return
        payload: dict[str, Any] = {
            "role": role,
            "content": content.strip(),
            "message_type": message_type,
        }
        if turn_index is not None:
            payload["turn_index"] = turn_index
        if metadata:
            payload.update(metadata)
        result.workflow.messages.append(payload)

    @staticmethod
    def render_decision_transcript(result: CaseRunResult) -> str:
        role_labels = {
            "user": "用户",
            "assistant": "助手",
            "agent": "助手",
            "system": "系统",
        }
        lines: list[str] = []
        for item in result.workflow.messages:
            if not isinstance(item, dict):
                continue
            role = role_labels.get(str(item.get("role") or "").lower(), str(item.get("role") or "未知"))
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            turn_prefix = ""
            if isinstance(item.get("turn_index"), int):
                turn_prefix = f"第{item['turn_index']}轮"
            message_type = str(item.get("message_type") or "").strip()
            label = role
            if turn_prefix:
                label = f"{turn_prefix}{label}"
            if message_type == "ask_user":
                label = f"{label}(澄清提问)"
            elif message_type == "structured_decision":
                label = f"{label}(结构化决策)"
            lines.append(f"{label}: {content}")
            reason = str(item.get("reason") or "").strip()
            if reason:
                lines.append(f"{label}补充: {reason}")
        return "\n".join(lines).strip() or "当前还没有历史对话。"

    @staticmethod
    def build_scenario_instruction(task: TaskCase, result: CaseRunResult) -> str:
        config = task.user_simulation_config
        rollback_declared = any(
            turn.user_decision_kind == "declare_rollback_intent" for turn in result.workflow.turns
        )
        lines = [sanitize_instruction_text(task.instruction)]
        lines.append("")
        lines.append("当前用户场景补充：")
        lines.append(f"- 场景名：{config.scenario}")
        lines.append(f"- 撤回意图模式：{config.rollback_intent_mode}")
        lines.append(f"- 滞后撤回最少间隔轮次：{config.rollback_min_round_gap}")
        lines.append(f"- 是否已经表达过撤回意图：{'是' if rollback_declared else '否'}")
        if task.user_profile and task.user_profile.persona:
            lines.append(f"- 用户人格：{task.user_profile.persona}")
        if task.user_profile and task.user_profile.correction_style:
            lines.append(f"- 纠错风格：{task.user_profile.correction_style}")
        if config.notes:
            lines.append(f"- 场景补充说明：{config.notes}")
        return "\n".join(lines).strip()

    @staticmethod
    def resolve_selected_option(
        options: list[ResolvedAskUserOption],
        *,
        selected_option_key: str | None,
        selected_option_label: str | None,
    ) -> ResolvedAskUserOption | None:
        normalized_key = str(selected_option_key or "").strip()
        normalized_label = str(selected_option_label or "").strip()
        for option in options:
            if normalized_key and option.key == normalized_key:
                return option
        for option in options:
            if normalized_label and option.label == normalized_label:
                return option
        return None

    @staticmethod
    def build_turn_record(
        *,
        benchmark_track: str,
        turn_index: int,
        request_kind: str,
        adapter_result: AdapterResult,
    ) -> BenchmarkTurnRecord:
        body = adapter_result.raw_body if isinstance(adapter_result.raw_body, dict) else None
        response_type = str(body.get("type") or "") if body else ""
        if body and not response_type:
            response_type, _, _, _, _ = normalize_documents(benchmark_track, body)
        if adapter_result.error_message and not response_type:
            response_type = "error"
        ask_user = extract_ask_user_payload(body)
        option_snapshot = [
            {
                "key": option.key,
                "label": option.label,
                "description": option.description,
                "selection_payload": dict(option.selection_payload),
            }
            for option in normalize_ask_user_options(body)
        ]
        is_terminal = bool(adapter_result.error_message) or response_type in {"documents", "message", "error"}
        stop_reason = response_type if response_type else ("error" if adapter_result.error_message else None)
        return BenchmarkTurnRecord(
            turn_index=turn_index,
            request_kind=request_kind,
            request_payload=dict(adapter_result.request_payload),
            response_http_status=adapter_result.http_status,
            response_body=body,
            response_type=response_type,
            session_id=str(body.get("session_id")) if body and body.get("session_id") is not None else None,
            business=str(body.get("business")) if body and body.get("business") is not None else None,
            tool_call_id=str(ask_user.get("tool_call_id")) if ask_user.get("tool_call_id") is not None else None,
            ask_user_question=str(ask_user.get("question")) if ask_user.get("question") is not None else None,
            clarify_options_snapshot=option_snapshot,
            is_terminal=is_terminal,
            stop_reason=stop_reason,
        )

    @staticmethod
    def build_case_context(
        task: TaskCase,
        result: CaseRunResult | None = None,
        *,
        turn_index: int | None = None,
    ) -> list[tuple[str, Any]]:
        pairs: list[tuple[str, Any]] = [("case", task.case_id)]
        if result is not None:
            pairs.append(("attempt", result.attempt_index))
        if turn_index is not None:
            pairs.append(("turn", turn_index))
        return pairs

    def log_case_event(
        self,
        task: TaskCase,
        result: CaseRunResult | None,
        event: str,
        *,
        level: str = "信息",
        turn_index: int | None = None,
        result_fields: list[tuple[str, Any]] | None = None,
        detail: Any = None,
        path: Any = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.runtime_logger.emit(
            event,
            level=level,
            context=self.build_case_context(task, result, turn_index=turn_index),
            result=result_fields,
            detail=detail,
            path=path,
            payload=payload,
        )

    def log_user_decision_trace(
        self,
        task: TaskCase,
        result: CaseRunResult,
        turn: BenchmarkTurnRecord,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        result_fields: list[tuple[str, Any]] = []
        detail_parts: list[str] = []
        internal_attempt = payload.get("internal_attempt")
        attempt_limit = payload.get("attempt_limit")
        if internal_attempt is not None and attempt_limit is not None:
            result_fields.append(("内部尝试", f"{internal_attempt}/{attempt_limit}"))
        if payload.get("strategy"):
            result_fields.append(("策略", payload.get("strategy")))
        if payload.get("model"):
            result_fields.append(("模型", payload.get("model")))

        if event == "用户模拟输出非法":
            error_text = clip_text(str(payload.get("error") or ""), limit=180)
            raw_text = clip_text(str(payload.get("raw_text") or ""), limit=180)
            if error_text:
                detail_parts.append(f"错误={error_text}")
            if raw_text:
                detail_parts.append(f"原始输出={raw_text}")
        elif event == "用户模拟校验失败":
            error_text = clip_text(str(payload.get("error") or ""), limit=180)
            raw_text = clip_text(str(payload.get("raw_text") or ""), limit=180)
            if error_text:
                detail_parts.append(f"校验错误={error_text}")
            if raw_text:
                detail_parts.append(f"原始输出={raw_text}")
        elif event == "用户模拟符号决策":
            if payload.get("decision_kind"):
                result_fields.append(("决策类型", payload.get("decision_kind")))
            if payload.get("selected_option_label") or payload.get("selected_option_key"):
                result_fields.append(
                    ("选择", payload.get("selected_option_label") or payload.get("selected_option_key"))
                )
            if payload.get("stop_reason_code"):
                result_fields.append(("stop_reason_code", payload.get("stop_reason_code")))
            reason_text = clip_text(str(payload.get("reason") or ""), limit=180)
            if reason_text:
                detail_parts.append(f"原因={reason_text}")

        self.log_case_event(
            task,
            result,
            event,
            turn_index=turn.turn_index,
            result_fields=result_fields,
            detail=detail_parts or None,
            payload=payload,
        )

    def record_turn(
        self,
        *,
        task: TaskCase,
        result: CaseRunResult,
        request_kind: str,
        turn_index: int,
        adapter_result: AdapterResult,
    ) -> BenchmarkTurnRecord:
        result.execution.endpoint = adapter_result.endpoint
        result.execution.http_status = adapter_result.http_status
        raw_path = self.write_raw_response(
            task,
            attempt_index=result.attempt_index,
            turn_index=turn_index,
            request_kind=request_kind,
            body=adapter_result.raw_body,
        )
        if raw_path:
            result.artifacts.raw_response_path = raw_path
            result.artifacts.raw_response_paths.append(raw_path)

        turn = self.build_turn_record(
            benchmark_track=task.benchmark_track,
            turn_index=turn_index,
            request_kind=request_kind,
            adapter_result=adapter_result,
        )
        result.workflow.turns.append(turn)
        self.refresh_workflow_counters(result)

        if turn.session_id:
            result.execution.session_id = turn.session_id

        assistant_text = summarize_response_content(
            turn.response_body,
            turn.response_type,
            adapter_result.error_message,
        )
        if assistant_text:
            self.append_workflow_message(
                result,
                role="assistant",
                content=assistant_text,
                message_type="ask_user" if turn.response_type == "ask_user" else "agent_response",
                turn_index=turn.turn_index,
            )
            if turn.response_type in {"documents", "message"}:
                result.workflow.final_agent_response = assistant_text

        detail_parts: list[str] = []
        response_type = turn.response_type or ("error" if adapter_result.error_message else "")
        if response_type == "ask_user":
            question_text = clip_text(turn.ask_user_question, limit=160)
            if question_text:
                detail_parts.append(f"提问={question_text}")
            if turn.clarify_options_snapshot:
                detail_parts.append(f"选项数={len(turn.clarify_options_snapshot)}")
                option_summary = summarize_option_labels(turn.clarify_options_snapshot)
                if option_summary:
                    detail_parts.append(f"候选项={option_summary}")
        elif response_type == "documents" and isinstance(turn.response_body, dict):
            _, docs, predicted_pages, _, locator_summary = normalize_documents(
                task.benchmark_track,
                turn.response_body,
                target_docs=list(getattr(task, "target_docs", None) or []),
                matched_titles=list(getattr(task, "accepted_titles", None) or []),
            )
            detail_parts.append(f"文档数={len(docs)}")
            doc_summary = summarize_document_titles(docs)
            if doc_summary:
                detail_parts.append(f"Top文档={doc_summary}")
            page_summary = summarize_int_list(predicted_pages)
            if page_summary:
                detail_parts.append(f"页码={page_summary}")
            if locator_summary.get("locator_status"):
                detail_parts.append(f"定位状态={locator_summary['locator_status']}")
        elif response_type == "message":
            message_text = clip_text(assistant_text, limit=180)
            if message_text:
                detail_parts.append(f"消息={message_text}")
        elif adapter_result.error_message:
            error_text = clip_text(adapter_result.error_message, limit=180)
            if error_text:
                detail_parts.append(f"错误={error_text}")

        self.log_case_event(
            task,
            result,
            "收到响应",
            turn_index=turn.turn_index,
            result_fields=[("HTTP", adapter_result.http_status), ("响应类型", response_type or "-")],
            detail=detail_parts or None,
            path=[("原始响应", raw_path)] if raw_path else None,
            payload={
                "response_type": response_type,
                "error_message": adapter_result.error_message,
            },
        )
        if response_type == "ask_user":
            question_text = clip_text(turn.ask_user_question, limit=160)
            option_summary = summarize_option_labels(turn.clarify_options_snapshot)
            ask_user_detail = []
            if question_text:
                ask_user_detail.append(f"提问={question_text}")
            if option_summary:
                ask_user_detail.append(f"候选项={option_summary}")
            self.log_case_event(
                task,
                result,
                "识别澄清问题",
                turn_index=turn.turn_index,
                result_fields=[
                    ("选项数", len(turn.clarify_options_snapshot)),
                    ("tool_call_id", turn.tool_call_id or "-"),
                ],
                detail=ask_user_detail or None,
                payload={"option_count": len(turn.clarify_options_snapshot)},
            )
        return turn

    def stop_attempt(
        self,
        task: TaskCase,
        result: CaseRunResult,
        *,
        response_type: str,
        final_status: str,
        stop_reason: str,
        raw_summary: str,
        conversation_completed: bool,
    ) -> None:
        result.response.response_type = response_type
        result.response.final_status = final_status
        result.response.raw_summary = raw_summary
        result.workflow.stop_reason = stop_reason
        result.workflow.conversation_completed = conversation_completed
        result.workflow.stopped_by_user_simulation = stop_reason == "user_simulation_stop"
        result.workflow.simulation_stop_count = sum(
            1 for turn in result.workflow.turns if turn.user_decision_kind == "stop"
        )
        self.refresh_workflow_counters(result)
        self.log_case_event(
            task,
            result,
            "尝试停止",
            level="警告" if stop_reason != "error" else "错误",
            result_fields=[
                ("响应类型", response_type),
                ("final_status", final_status),
                ("stop_reason", stop_reason),
            ],
            detail=clip_text(raw_summary, limit=220),
            payload={"stop_reason": stop_reason},
        )

    def populate_terminal_response(
        self,
        *,
        task: TaskCase,
        result: CaseRunResult,
        adapter_result: AdapterResult,
    ) -> None:
        body = adapter_result.raw_body or {}
        response_type, docs, predicted_pages, page_confidence, locator_summary = normalize_documents(
            task.benchmark_track,
            body,
            target_docs=list(getattr(task, "target_docs", None) or []),
            matched_titles=list(getattr(task, "accepted_titles", None) or []),
        )
        result.response.response_type = response_type
        result.response.final_status = "error_http" if adapter_result.error_message else (
            "success_documents" if docs else "success_message"
        )
        result.response.raw_summary = summarize_response_content(
            body,
            response_type,
            adapter_result.error_message,
        ) or str(body.get("business") or body.get("status") or "")
        result.response.business = str(body.get("business") or result.response.business or "DOC_SEARCH")
        result.prediction.top_k_documents = docs
        result.prediction.predicted_pages = predicted_pages
        result.prediction.page_confidence = page_confidence
        apply_locator_prediction(result, locator_summary)
        result.workflow.stop_reason = response_type or "error"
        result.workflow.conversation_completed = True
        self.refresh_workflow_counters(result)

    def request_structured_decision(
        self,
        *,
        task: TaskCase,
        result: CaseRunResult,
        turn: BenchmarkTurnRecord,
        options: list[ResolvedAskUserOption],
    ):
        instruction = self.build_scenario_instruction(task, result)
        transcript = self.render_decision_transcript(result)
        ask_user_payload = extract_ask_user_payload(turn.response_body)
        context = AskUserDecisionContext(
            ask_user_question=turn.ask_user_question or "",
            options=[
                AskUserOption(
                    key=option.key,
                    label=option.label,
                    description=option.description,
                )
                for option in options
            ],
            conversation_turn_count=len(result.workflow.turns),
            scenario=task.user_simulation_config.scenario,
            initial_user_message=task.initial_user_message or task.question_text,
            user_profile=task.user_profile,
            ask_user_context=build_decision_ask_user_context(ask_user_payload),
        )
        def trace_hook(event: str, payload: dict[str, Any]) -> None:
            self.log_user_decision_trace(task, result, turn, event, payload)
        return generate_structured_user_decision(
            user_strategy=self.config.user_strategy,
            model=self.config.user_model,
            provider=self.config.user_provider,
            prompt=instruction,
            context=context,
            instruction=instruction,
            transcript=transcript,
            trace_hook=trace_hook,
        )

    def run_search_api_case(self, task: TaskCase, result: CaseRunResult) -> None:
        call = self.adapter.build_call(task)
        result.execution.endpoint = call.endpoint
        self.log_case_event(
            task,
            result,
            "发送请求",
            turn_index=1,
            result_fields=[("请求类型", "search_api"), ("接口", call.endpoint)],
            detail=f"查询={clip_text(task.question_text, limit=180)}",
            payload={"request_kind": "search_api"},
        )
        adapter_result = self.adapter.execute(call)
        self.record_turn(
            task=task,
            result=result,
            request_kind="search_api",
            turn_index=1,
            adapter_result=adapter_result,
        )

        if adapter_result.error_message:
            result.response.response_type = "error"
            result.response.final_status = "error_http"
            result.response.raw_summary = adapter_result.error_message
            result.workflow.stop_reason = "error"
            return

        body = adapter_result.raw_body or {}
        response_type, docs, predicted_pages, page_confidence, locator_summary = normalize_documents(
            task.benchmark_track,
            body,
            target_docs=list(getattr(task, "target_docs", None) or []),
            matched_titles=list(getattr(task, "accepted_titles", None) or []),
        )
        result.response.response_type = response_type
        result.response.final_status = "success_documents" if docs else "success_message"
        result.response.raw_summary = str(body.get("status") or "")
        result.prediction.top_k_documents = docs
        result.prediction.predicted_pages = predicted_pages
        result.prediction.page_confidence = page_confidence
        apply_locator_prediction(result, locator_summary)
        result.workflow.stop_reason = response_type

    def run_chat_case(self, task: TaskCase, result: CaseRunResult) -> None:
        initial_message = task.initial_user_message or task.question_text
        self.append_workflow_message(
            result,
            role="user",
            content=initial_message,
            message_type="initial_message",
        )

        initial_call = self.adapter.build_initial_chat_call(task)
        initial_request_kind = "initial_message_with_images" if task.question_images else "initial_message"
        self.log_case_event(
            task,
            result,
            "发送请求",
            turn_index=1,
            result_fields=[("请求类型", "initial_message"), ("接口", initial_call.endpoint)],
            detail=f"首轮消息={clip_text(initial_message, limit=180)}",
            payload={"request_kind": initial_request_kind},
        )
        adapter_result = self.adapter.execute(initial_call)
        last_turn = self.record_turn(
            task=task,
            result=result,
            request_kind=initial_request_kind,
            turn_index=1,
            adapter_result=adapter_result,
        )

        while True:
            if last_turn.is_terminal:
                self.populate_terminal_response(task=task, result=result, adapter_result=adapter_result)
                return

            if last_turn.response_type != "ask_user":
                last_turn.stop_reason = "error"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "error",
                    final_status="stopped_unexpected_response",
                    stop_reason="error",
                    raw_summary="chat_completions 返回了无法继续消费的中间响应。",
                    conversation_completed=False,
                )
                return

            if len(result.workflow.turns) >= task.max_turns:
                last_turn.stop_reason = "max_turns_exceeded"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_max_turns_exceeded",
                    stop_reason="max_turns_exceeded",
                    raw_summary="超过 max_turns 仍未进入终态。",
                    conversation_completed=False,
                )
                return

            options = normalize_ask_user_options(last_turn.response_body)
            if not last_turn.session_id:
                last_turn.stop_reason = "missing_session_id"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_missing_session_id",
                    stop_reason="missing_session_id",
                    raw_summary="ask_user 响应缺少 session_id。",
                    conversation_completed=False,
                )
                return

            if not last_turn.tool_call_id:
                last_turn.stop_reason = "missing_tool_call_id"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_missing_tool_call_id",
                    stop_reason="missing_tool_call_id",
                    raw_summary="ask_user 响应缺少 tool_call_id。",
                    conversation_completed=False,
                )
                return

            if not options:
                last_turn.stop_reason = "missing_selection_payload"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_missing_selection_payload",
                    stop_reason="missing_selection_payload",
                    raw_summary="ask_user 响应没有可消费选项。",
                    conversation_completed=False,
                )
                return

            question_text = clip_text(last_turn.ask_user_question, limit=160)
            option_summary = summarize_option_labels(options)
            self.log_case_event(
                task,
                result,
                "开始用户模拟决策",
                turn_index=last_turn.turn_index,
                result_fields=[
                    ("策略", self.config.user_strategy),
                    ("模型", self.config.user_model or "-"),
                    ("选项数", len(options)),
                ],
                detail=[
                    f"提问={question_text}" if question_text else "",
                    f"候选项={option_summary}" if option_summary else "",
                ],
            )
            decision_started = perf_counter()
            try:
                decision = self.request_structured_decision(
                    task=task,
                    result=result,
                    turn=last_turn,
                    options=options,
                )
            except UserSimulationProviderError as exc:
                elapsed_ms = round((perf_counter() - decision_started) * 1000.0, 3)
                last_turn.user_decision_source = task.user_simulation_config.driver
                last_turn.user_decision_kind = "error"
                last_turn.user_decision_reason = str(exc)
                last_turn.stop_reason = "error"
                result.response.response_type = "error"
                self.log_case_event(
                    task,
                    result,
                    "用户模拟决策失败",
                    level="错误",
                    turn_index=last_turn.turn_index,
                    result_fields=[
                        ("策略", self.config.user_strategy),
                        ("模型", self.config.user_model or "-"),
                        ("耗时ms", elapsed_ms),
                    ],
                    detail=clip_text(str(exc), limit=220),
                )
                self.stop_attempt(
                    task,
                    result,
                    response_type="error",
                    final_status="error_http",
                    stop_reason="error",
                    raw_summary=str(exc),
                    conversation_completed=False,
                )
                return
            except Exception as exc:
                elapsed_ms = round((perf_counter() - decision_started) * 1000.0, 3)
                last_turn.user_decision_source = task.user_simulation_config.driver
                last_turn.user_decision_kind = "invalid"
                last_turn.user_decision_reason = str(exc)
                last_turn.stop_reason = "invalid_user_decision"
                self.log_case_event(
                    task,
                    result,
                    "用户模拟决策失败",
                    level="错误",
                    turn_index=last_turn.turn_index,
                    result_fields=[
                        ("策略", self.config.user_strategy),
                        ("模型", self.config.user_model or "-"),
                        ("耗时ms", elapsed_ms),
                    ],
                    detail=clip_text(str(exc), limit=220),
                )
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_invalid_user_decision",
                    stop_reason="invalid_user_decision",
                    raw_summary=str(exc),
                    conversation_completed=False,
                )
                return
            elapsed_ms = round((perf_counter() - decision_started) * 1000.0, 3)
            decision_detail: list[str] = []
            reason_text = clip_text(decision.reason, limit=180)
            if reason_text:
                decision_detail.append(f"原因={reason_text}")
            if decision.selected_option_label or decision.selected_option_key:
                decision_detail.append(
                    f"选择={decision.selected_option_label or decision.selected_option_key}"
                )
            if decision.rollback_target_round is not None:
                decision_detail.append(f"撤回目标轮次={decision.rollback_target_round}")
            if decision.stop_reason_code:
                decision_detail.append(f"stop_reason_code={decision.stop_reason_code}")
            decision_detail.extend(summarize_evidence(decision.evidence))
            self.log_case_event(
                task,
                result,
                "完成用户模拟决策",
                turn_index=last_turn.turn_index,
                result_fields=[
                    ("决策类型", decision.decision_kind),
                    ("策略", self.config.user_strategy),
                    ("模型", self.config.user_model or "-"),
                    ("耗时ms", elapsed_ms),
                ],
                detail=decision_detail or None,
                payload={"decision_kind": decision.decision_kind},
            )

            last_turn.user_decision_source = task.user_simulation_config.driver
            last_turn.user_decision_kind = decision.decision_kind
            last_turn.user_decision_reason = decision.reason
            last_turn.user_stop_reason_code = decision.stop_reason_code
            last_turn.user_decision_evidence = dict(decision.evidence)

            if decision.decision_kind == "declare_rollback_intent":
                rollback_text = f"我想撤回到第 {decision.rollback_target_round} 轮重新选择。"
                last_turn.rollback_intent_mode = task.user_simulation_config.rollback_intent_mode
                last_turn.rollback_target_round = decision.rollback_target_round
                last_turn.rollback_supported = False
                last_turn.capability_gap = ROLLBACK_UNSUPPORTED_GAP
                last_turn.user_response_text = rollback_text
                last_turn.stop_reason = "rollback_unsupported"
                result.workflow.final_user_response = rollback_text
                if ROLLBACK_UNSUPPORTED_GAP not in result.workflow.capability_gaps:
                    result.workflow.capability_gaps.append(ROLLBACK_UNSUPPORTED_GAP)
                self.log_case_event(
                    task,
                    result,
                    "发现能力缺口",
                    level="警告",
                    turn_index=last_turn.turn_index,
                    result_fields=[
                        ("能力缺口", ROLLBACK_UNSUPPORTED_GAP),
                        ("撤回目标轮次", decision.rollback_target_round),
                    ],
                    detail=f"用户回复={clip_text(rollback_text, limit=120)}",
                )
                self.append_workflow_message(
                    result,
                    role="user",
                    content=rollback_text,
                    message_type="structured_decision",
                    turn_index=last_turn.turn_index,
                    metadata={
                        "decision_kind": decision.decision_kind,
                        "reason": decision.reason,
                        "rollback_target_round": decision.rollback_target_round,
                    },
                )
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_rollback_unsupported",
                    stop_reason="rollback_unsupported",
                    raw_summary=ROLLBACK_UNSUPPORTED_GAP,
                    conversation_completed=False,
                )
                return

            if decision.decision_kind == "stop":
                stop_text = decision.user_message or "当前用户模拟选择结束该 case。"
                last_turn.user_response_text = stop_text
                last_turn.stop_reason = "user_simulation_stop"
                result.workflow.final_user_response = stop_text
                self.append_workflow_message(
                    result,
                    role="user",
                    content=stop_text,
                    message_type="structured_decision",
                    turn_index=last_turn.turn_index,
                    metadata={
                        "decision_kind": decision.decision_kind,
                        "reason": decision.reason,
                        "stop_reason_code": decision.stop_reason_code,
                        "evidence": dict(decision.evidence),
                    },
                )
                self.log_case_event(
                    task,
                    result,
                    "用户模拟触发早停",
                    level="警告",
                    turn_index=last_turn.turn_index,
                    result_fields=[
                        ("stop_reason_code", decision.stop_reason_code or "-"),
                        ("决策类型", decision.decision_kind),
                    ],
                    detail=(
                        [f"原因={reason_text}"] if reason_text else []
                    ) + summarize_evidence(decision.evidence),
                    payload={
                        "decision_kind": decision.decision_kind,
                        "stop_reason_code": decision.stop_reason_code,
                    },
                )
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_by_user_simulation",
                    stop_reason="user_simulation_stop",
                    raw_summary=decision.reason or decision.stop_reason_code or stop_text,
                    conversation_completed=False,
                )
                return

            if decision.decision_kind != "choose_option":
                last_turn.user_response_text = decision.user_message
                last_turn.stop_reason = "invalid_user_decision"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_invalid_user_decision",
                    stop_reason="invalid_user_decision",
                    raw_summary=f"ask_user 轮返回了不支持的决策类型：{decision.decision_kind}",
                    conversation_completed=False,
                )
                return

            selected_option = self.resolve_selected_option(
                options,
                selected_option_key=decision.selected_option_key,
                selected_option_label=decision.selected_option_label,
            )
            if selected_option is None:
                last_turn.stop_reason = "invalid_user_decision"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_invalid_user_decision",
                    stop_reason="invalid_user_decision",
                    raw_summary="AI 用户返回了不存在的选项。",
                    conversation_completed=False,
                )
                return

            if not isinstance(selected_option.selection_payload, dict):
                last_turn.stop_reason = "missing_selection_payload"
                self.stop_attempt(
                    task,
                    result,
                    response_type=last_turn.response_type or "ask_user",
                    final_status="stopped_missing_selection_payload",
                    stop_reason="missing_selection_payload",
                    raw_summary="选中项缺少 selection_payload。",
                    conversation_completed=False,
                )
                return

            answer_text = selected_option.label or selected_option.key
            last_turn.selected_option_key = selected_option.key
            last_turn.selected_option_label = selected_option.label
            last_turn.selected_selection_payload = dict(selected_option.selection_payload)
            last_turn.user_response_text = answer_text
            result.workflow.final_user_response = answer_text
            self.log_case_event(
                task,
                result,
                "用户选择已提交",
                turn_index=last_turn.turn_index,
                result_fields=[
                    ("选择key", selected_option.key),
                    ("选择label", selected_option.label),
                ],
                detail=f"原因={reason_text}" if reason_text else None,
            )
            self.append_workflow_message(
                result,
                role="user",
                content=answer_text,
                message_type="structured_decision",
                turn_index=last_turn.turn_index,
                metadata={
                    "decision_kind": decision.decision_kind,
                    "reason": decision.reason,
                    "selected_option_key": selected_option.key,
                    "selected_option_label": selected_option.label,
                    "evidence": dict(decision.evidence),
                },
            )

            resume_call = self.adapter.build_resume_chat_call(
                session_id=last_turn.session_id,
                tool_call_id=last_turn.tool_call_id,
                answer=answer_text,
                selection_payload=selected_option.selection_payload,
            )
            next_turn_index = len(result.workflow.turns) + 1
            self.log_case_event(
                task,
                result,
                "发送请求",
                turn_index=next_turn_index,
                result_fields=[("请求类型", "ask_user_resume"), ("接口", resume_call.endpoint)],
                detail=f"用户回答={clip_text(answer_text, limit=160)}",
                payload={"request_kind": "ask_user_resume"},
            )
            adapter_result = self.adapter.execute(resume_call)
            last_turn = self.record_turn(
                task=task,
                result=result,
                request_kind="ask_user_resume",
                turn_index=next_turn_index,
                adapter_result=adapter_result,
            )

    def run_case(self, task: TaskCase, *, attempt_index: int = 1) -> CaseRunResult:
        result = build_case_run_result(task, self.run_id, attempt_index=attempt_index)
        result.execution.started_at = now_iso()

        self.log_case_event(
            task,
            result,
            "请求预处理",
            result_fields=[("输入类型", task.input_modality), ("track", task.benchmark_track)],
        )
        preprocess = prepare_request_context(task)
        result.workflow.used_image_context = preprocess.used_image_context
        result.validation.blocking_failures.extend(preprocess.blocking_failures)
        result.validation.warnings.extend(preprocess.warnings)

        if preprocess.blocking_failures:
            result.response.response_type = "skipped"
            result.response.final_status = "skipped_preprocess_contract_missing"
            result.response.raw_summary = "request context missing for image preprocess contract"
            result.workflow.stop_reason = "preprocess_blocked"
            self.log_case_event(
                task,
                result,
                "预处理阻断",
                level="警告",
                result_fields=[
                    ("使用图片上下文", preprocess.used_image_context),
                    ("阻断数", len(preprocess.blocking_failures)),
                    ("告警数", len(preprocess.warnings)),
                ],
                detail=[
                    f"阻断={summarize_codes(preprocess.blocking_failures)}"
                    if summarize_codes(preprocess.blocking_failures)
                    else "",
                    f"告警={summarize_codes(preprocess.warnings)}"
                    if summarize_codes(preprocess.warnings)
                    else "",
                ],
                payload={"stop_reason": "preprocess_blocked"},
            )
        else:
            self.log_case_event(
                task,
                result,
                "预处理完成",
                result_fields=[
                    ("使用图片上下文", preprocess.used_image_context),
                    ("阻断数", len(preprocess.blocking_failures)),
                    ("告警数", len(preprocess.warnings)),
                ],
            )
            runtime_task = task
            if preprocess.request_context != task.request_context:
                runtime_task = replace(task, request_context=preprocess.request_context)

            if runtime_task.benchmark_track == "search_api":
                self.run_search_api_case(runtime_task, result)
            else:
                self.run_chat_case(runtime_task, result)

        contract_outcome = judge_contract(task, result)
        result.validation.schema_pass = contract_outcome["schema_pass"]
        result.validation.blocking_failures.extend(contract_outcome["blocking_failures"])
        result.validation.warnings.extend(contract_outcome["warnings"])
        self.log_case_event(
            task,
            result,
            "合同判定完成",
            result_fields=[
                ("schema_pass", contract_outcome["schema_pass"]),
                ("阻断新增", len(contract_outcome["blocking_failures"])),
                ("告警新增", len(contract_outcome["warnings"])),
            ],
            detail=[
                f"阻断={summarize_codes(contract_outcome['blocking_failures'])}"
                if summarize_codes(contract_outcome["blocking_failures"])
                else "",
                f"告警={summarize_codes(contract_outcome['warnings'])}"
                if summarize_codes(contract_outcome["warnings"])
                else "",
            ],
        )

        file_outcome = judge_file(task, result)
        multi_target_payload = attach_multi_target_runtime_fields(task, result, file_outcome)
        result.metrics.recall_hit = file_outcome["recall_hit"]
        result.metrics.hit_at_1 = file_outcome["hit_at_1"]
        result.metrics.hit_at_3 = file_outcome["hit_at_3"]
        result.metrics.mrr = file_outcome["mrr"]
        result.validation.blocking_failures.extend(file_outcome["blocking_failures"])
        result.validation.warnings.extend(file_outcome["warnings"])
        self.log_case_event(
            task,
            result,
            "文件判定完成",
            result_fields=[
                ("recall_hit", file_outcome["recall_hit"]),
                ("hit_at_1", file_outcome["hit_at_1"]),
                ("hit_at_3", file_outcome["hit_at_3"]),
                ("mrr", round(float(file_outcome["mrr"]), 6)),
                ("target_match_mode", multi_target_payload["target_match_mode"]),
                ("matched_target_count", multi_target_payload["matched_target_count"]),
                ("target_doc_count", multi_target_payload["target_doc_count"]),
                ("target_coverage_rate", multi_target_payload["target_coverage_rate"]),
            ],
            detail=[
                (
                    f"matched_targets={summarize_codes(multi_target_payload['matched_targets'])}"
                    if summarize_codes(multi_target_payload["matched_targets"])
                    else ""
                ),
                (
                    f"missed_targets={summarize_codes(multi_target_payload['missed_targets'])}"
                    if summarize_codes(multi_target_payload["missed_targets"])
                    else ""
                ),
                f"阻断={summarize_codes(file_outcome['blocking_failures'])}"
                if summarize_codes(file_outcome["blocking_failures"])
                else "",
                f"告警={summarize_codes(file_outcome['warnings'])}"
                if summarize_codes(file_outcome["warnings"])
                else "",
            ],
            payload={
                "recall_hit": file_outcome["recall_hit"],
                "target_match_mode": multi_target_payload["target_match_mode"],
                "matched_target_count": multi_target_payload["matched_target_count"],
                "target_doc_count": multi_target_payload["target_doc_count"],
                "target_coverage_rate": multi_target_payload["target_coverage_rate"],
            },
        )

        page_outcome = judge_page(task, result)
        result.metrics.page_hit_at_1 = page_outcome["page_hit_at_1"]
        result.metrics.page_hit_at_k = page_outcome["page_hit_at_k"]
        result.metrics.exact_page_hit = page_outcome["exact_page_hit"]
        result.metrics.page_range_overlap_hit = page_outcome["page_range_overlap_hit"]
        result.metrics.min_page_distance = page_outcome["min_page_distance"]
        result.validation.warnings.extend(page_outcome["warnings"])
        self.log_case_event(
            task,
            result,
            "页码判定完成",
            result_fields=[
                ("page_hit_at_1", page_outcome["page_hit_at_1"]),
                ("page_hit_at_k", page_outcome["page_hit_at_k"]),
                ("exact_page_hit", page_outcome["exact_page_hit"]),
                ("min_page_distance", page_outcome["min_page_distance"]),
            ],
            detail=(
                f"告警={summarize_codes(page_outcome['warnings'])}"
                if summarize_codes(page_outcome["warnings"])
                else None
            ),
            payload={"page_hit_at_k": page_outcome["page_hit_at_k"]},
        )

        locator_outcome = judge_locator(task, result)
        result.metrics.locator_hit_at_1 = locator_outcome["locator_hit_at_1"]
        result.metrics.locator_hit_at_k = locator_outcome["locator_hit_at_k"]
        result.metrics.locator_exact_page_hit = locator_outcome["locator_exact_page_hit"]
        result.metrics.locator_range_overlap_hit = locator_outcome["locator_range_overlap_hit"]
        result.metrics.locator_min_page_distance = locator_outcome["locator_min_page_distance"]
        result.metrics.locator_source = locator_outcome["locator_source"]
        result.metrics.locator_status = locator_outcome["locator_status"]
        result.metrics.locator_best_page = locator_outcome["locator_best_page"]
        result.metrics.locator_top_pages = list(locator_outcome["locator_top_pages"])
        result.metrics.locator_viewer_token_present = result.prediction.locator_viewer_token_present
        result.metrics.locator_preview_present = result.prediction.locator_preview_present
        result.metrics.locator_eligible = locator_outcome["eligible"]
        result.metrics.locator_document_level_failure = locator_outcome["document_level_failure"]
        result.metrics.document_hit = locator_outcome["document_hit"]
        result.metrics.document_hit_eligible = locator_outcome["document_hit_eligible"]
        result.metrics.document_level_failure = locator_outcome["document_level_failure"]
        result.validation.warnings.extend(locator_outcome["warnings"])
        self.log_case_event(
            task,
            result,
            "定位判定完成",
            result_fields=[
                ("locator_hit_at_1", locator_outcome["locator_hit_at_1"]),
                ("locator_hit_at_k", locator_outcome["locator_hit_at_k"]),
                ("exact_page_hit", locator_outcome["locator_exact_page_hit"]),
                ("range_overlap_hit", locator_outcome["locator_range_overlap_hit"]),
                ("best_page", locator_outcome["locator_best_page"]),
            ],
            detail="；".join(
                part
                for part in [
                    (
                        f"告警={summarize_codes(locator_outcome['warnings'])}"
                        if summarize_codes(locator_outcome["warnings"])
                        else ""
                    ),
                    (
                        f"失败={locator_outcome['document_level_failure']}"
                        if locator_outcome["document_level_failure"]
                        else ""
                    ),
                ]
                if part
            ) or None,
            payload={
                "locator_hit_at_k": locator_outcome["locator_hit_at_k"],
                "document_level_failure": locator_outcome["document_level_failure"],
            },
        )

        coord_outcome = judge_coord(task, result)
        result.metrics.coord_eligible = coord_outcome["eligible"]
        result.metrics.coord_hit = coord_outcome["coord_hit"]
        result.metrics.coord_hit_page_numbers = list(coord_outcome["coord_hit_page_numbers"])
        result.metrics.coord_hit_group_ids = list(coord_outcome["coord_hit_group_ids"])
        result.metrics.coord_failure_reason = coord_outcome["coord_failure_reason"]
        result.metrics.coord_metadata_present = coord_outcome["coord_metadata_present"]
        result.metrics.coord_viewer_token_present = coord_outcome["coord_viewer_token_present"]
        result.validation.warnings.extend(coord_outcome["warnings"])
        self.log_case_event(
            task,
            result,
            "坐标判定完成",
            result_fields=[
                ("coord_eligible", coord_outcome["eligible"]),
                ("coord_hit", coord_outcome["coord_hit"]),
                ("coord_failure_reason", coord_outcome["coord_failure_reason"]),
                ("coord_hit_pages", coord_outcome["coord_hit_page_numbers"]),
                ("coord_hit_groups", coord_outcome["coord_hit_group_ids"]),
            ],
            detail="；".join(
                part
                for part in [
                    (
                        f"告警={summarize_codes(coord_outcome['warnings'])}"
                        if summarize_codes(coord_outcome["warnings"])
                        else ""
                    ),
                    (
                        f"metadata_present={coord_outcome['coord_metadata_present']}"
                        if coord_outcome["coord_metadata_present"] is not None
                        else ""
                    ),
                ]
                if part
            ) or None,
            payload={
                "coord_hit": coord_outcome["coord_hit"],
                "coord_failure_reason": coord_outcome["coord_failure_reason"],
            },
        )

        result.validation.blocking_failures = sorted(set(result.validation.blocking_failures))
        result.validation.warnings = sorted(set(result.validation.warnings))
        trace_analysis = build_trace_analysis(task, result)
        result.analysis.final_hit = bool(trace_analysis["final_hit"])
        result.analysis.turn_count = int(trace_analysis["turn_count"])
        result.analysis.decision_trace = list(trace_analysis["decision_trace"])
        result.analysis.correction_count = int(trace_analysis["correction_count"])
        result.analysis.ambiguous_turn_count = int(trace_analysis["ambiguous_turn_count"])
        result.analysis.stop_reason = trace_analysis["stop_reason"]
        result.analysis.failure_reason = trace_analysis["failure_reason"]
        result.analysis.stopped_by_user_simulation = bool(trace_analysis["stopped_by_user_simulation"])
        result.analysis.simulation_stop_count = int(trace_analysis["simulation_stop_count"])
        result.analysis.simulation_valid_stop = trace_analysis["simulation_valid_stop"]
        result.analysis.user_stop_reason_code = trace_analysis["user_stop_reason_code"]
        upsert_multi_target_trace(result, multi_target_payload)
        self.log_case_event(
            task,
            result,
            "轨迹分析完成",
            result_fields=[
                ("final_hit", result.analysis.final_hit),
                ("turn_count", result.analysis.turn_count),
                ("failure_reason", result.analysis.failure_reason or "无"),
                ("target_match_mode", multi_target_payload["target_match_mode"]),
                ("coverage", multi_target_payload["target_coverage_rate"]),
            ],
            detail=[
                f"correction_count={result.analysis.correction_count}",
                f"ambiguous_turn_count={result.analysis.ambiguous_turn_count}",
                f"stop_reason={result.analysis.stop_reason or '无'}",
                f"user_stop_reason_code={result.analysis.user_stop_reason_code or '无'}",
                (
                    f"matched_targets={summarize_codes(multi_target_payload['matched_targets'])}"
                    if summarize_codes(multi_target_payload["matched_targets"])
                    else ""
                ),
                (
                    f"missed_targets={summarize_codes(multi_target_payload['missed_targets'])}"
                    if summarize_codes(multi_target_payload["missed_targets"])
                    else ""
                ),
            ],
            payload={
                "final_hit": result.analysis.final_hit,
                "target_match_mode": multi_target_payload["target_match_mode"],
                "matched_target_count": multi_target_payload["matched_target_count"],
                "target_doc_count": multi_target_payload["target_doc_count"],
                "target_coverage_rate": multi_target_payload["target_coverage_rate"],
            },
        )
        result.execution.ended_at = now_iso()
        if result.execution.started_at and result.execution.ended_at:
            started = datetime.fromisoformat(result.execution.started_at)
            ended = datetime.fromisoformat(result.execution.ended_at)
            result.execution.duration_ms = round((ended - started).total_seconds() * 1000.0, 3)

        result.validation.deterministic_hash = stable_hash(
            {
                "response": result.response,
                "prediction": result.prediction,
            }
        )
        self.log_case_event(
            task,
            result,
            "尝试完成",
            result_fields=[
                ("final_status", result.response.final_status),
                ("stop_reason", result.workflow.stop_reason or "无"),
                ("耗时ms", result.execution.duration_ms),
            ],
            detail=[
                f"阻断={summarize_codes(result.validation.blocking_failures)}"
                if summarize_codes(result.validation.blocking_failures)
                else "",
                f"告警={summarize_codes(result.validation.warnings)}"
                if summarize_codes(result.validation.warnings)
                else "",
                f"能力缺口={summarize_codes(result.workflow.capability_gaps)}"
                if summarize_codes(result.workflow.capability_gaps)
                else "",
            ],
        )
        return result
