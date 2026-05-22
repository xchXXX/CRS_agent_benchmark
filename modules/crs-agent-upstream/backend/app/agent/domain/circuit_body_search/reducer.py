"""Reduce noisy circuit body-search hits to ranked location candidates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from app.agent.domain.circuit_body_search.models import CircuitBodyBestHit, CircuitBodySearchSummary
from app.core.config import BACKEND_DIR, PROJECT_ROOT


MAX_TOP_HITS = 8
MAX_REGION_CANDIDATES = 24
MAX_EVIDENCE_TEXT_CHARS = 900


@dataclass
class _NormalizedHit:
    hit_id: str
    page_index: int
    reading_order: int
    element_index: int
    char_start: int
    matched_text: str
    context: str
    bbox: tuple[float, float, float, float] | None


@dataclass
class _OcrElement:
    text: str
    page_index: int
    reading_order: int
    bbox: tuple[float, float, float, float] | None


@dataclass
class _RegionCluster:
    page_index: int
    hits: list[_NormalizedHit] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None

    def add(self, hit: _NormalizedHit) -> None:
        self.hits.append(hit)
        if hit.bbox is None:
            return
        self.bbox = _merge_optional_boxes(self.bbox, hit.bbox)


class CircuitBodyHitReducer:
    """Build ranked body-search location candidates for backend result enrichment."""

    def reduce(
        self,
        raw_response: dict[str, Any],
        *,
        pdf_id: str,
        keyword: str,
        latest_result_path: str = "",
        document_title: str = "",
        max_top_hits: int = MAX_TOP_HITS,
    ) -> CircuitBodySearchSummary:
        if raw_response.get("status") == "failed":
            return CircuitBodySearchSummary(
                status="failed",
                reason=str(raw_response.get("error") or "external_search_failed"),
                pdf_id=pdf_id,
                keyword=keyword,
            )

        data = raw_response.get("data") if isinstance(raw_response.get("data"), dict) else raw_response
        if not isinstance(data, dict):
            return CircuitBodySearchSummary(
                status="failed",
                reason="invalid_external_response",
                pdf_id=pdf_id,
                keyword=keyword,
            )

        raw_results = data.get("results") or []
        if not isinstance(raw_results, list):
            raw_results = []

        hits = self._normalize_hits(raw_results)
        raw_hit_count = self._int_or(data.get("total_matches"), len(hits))
        if not hits:
            return CircuitBodySearchSummary(
                status="no_hit",
                reason="no_body_match",
                pdf_id=pdf_id,
                keyword=keyword,
                raw_hit_count=raw_hit_count,
            )

        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for hit in hits:
            grouped[hit.page_index].append(
                {
                    "hit_id": hit.hit_id,
                    "reading_order": hit.reading_order,
                    "element_index": hit.element_index,
                    "matched_text": hit.matched_text,
                    "context": hit.context,
                    "bbox": hit.bbox,
                }
            )

        page_hit_count = len(grouped)
        elements_by_page = self._load_ocr_elements(latest_result_path)
        candidates = self._build_region_candidates(
            hits=hits,
            elements_by_page=elements_by_page,
            keyword=keyword,
            pdf_id=pdf_id,
            document_title=document_title,
        )
        if not candidates:
            candidates = [self._fallback_best_hit(hits, keyword=keyword, pdf_id=pdf_id)]

        ranked = sorted(
            candidates,
            key=lambda hit: (
                -float(hit.score or 0.0),
                int(hit.page_index),
                int(hit.display_rank or 1_000_000),
                str(hit.hit_id),
            ),
        )
        max_hits = max(min(int(max_top_hits or MAX_TOP_HITS), MAX_TOP_HITS), 1)
        top_hits = ranked[:max_hits]
        for index, hit in enumerate(top_hits, start=1):
            hit.display_rank = index

        best_hit = top_hits[0] if top_hits else None
        return CircuitBodySearchSummary(
            status="hit",
            pdf_id=pdf_id,
            keyword=keyword,
            raw_hit_count=raw_hit_count,
            page_hit_count=page_hit_count,
            region_candidate_count=len(candidates),
            display_hit_count=len(top_hits),
            best_hit=best_hit,
            top_hits=top_hits,
            more_hits_count=max(raw_hit_count - len(top_hits), len(candidates) - len(top_hits), 0),
            rerank_source="rule",
        )

    def _normalize_hits(self, raw_results: list[Any]) -> list[_NormalizedHit]:
        hits: list[_NormalizedHit] = []
        for fallback_index, raw_hit in enumerate(raw_results):
            if not isinstance(raw_hit, dict):
                continue
            page_index = self._page_index(raw_hit)
            if page_index is None:
                continue
            reading_order = self._int_or(raw_hit.get("reading_order"), fallback_index)
            element_index = self._int_or(raw_hit.get("element_index"), fallback_index)
            char_start = self._int_or(raw_hit.get("char_start"), 0)
            hit_id = str(
                raw_hit.get("match_id")
                or raw_hit.get("hit_id")
                or f"p{page_index}_e{element_index}_c{char_start}"
            )
            hits.append(
                _NormalizedHit(
                    hit_id=hit_id,
                    page_index=page_index,
                    reading_order=reading_order,
                    element_index=element_index,
                    char_start=char_start,
                    matched_text=str(raw_hit.get("matched_text") or "").strip(),
                    context=self._context(raw_hit),
                    bbox=self._box(raw_hit),
                )
            )
        return hits

    def _build_region_candidates(
        self,
        *,
        hits: list[_NormalizedHit],
        elements_by_page: dict[int, list[_OcrElement]],
        keyword: str,
        pdf_id: str,
        document_title: str,
    ) -> list[CircuitBodyBestHit]:
        clusters = self._cluster_hits(hits)
        candidates: list[CircuitBodyBestHit] = []
        for cluster_index, cluster in enumerate(clusters[:MAX_REGION_CANDIDATES], start=1):
            ordered_hits = sorted(
                cluster.hits,
                key=lambda hit: (hit.reading_order, hit.element_index, hit.char_start, hit.hit_id),
            )
            representative = ordered_hits[0]
            evidence_text = self._nearby_ocr_text(
                page_elements=elements_by_page.get(cluster.page_index, []),
                region_bbox=cluster.bbox,
                representative=representative,
            )
            matched_texts = self._unique_texts(hit.matched_text for hit in ordered_hits)
            context_text = self._join_limited(
                self._unique_texts([*(hit.context for hit in ordered_hits), evidence_text]),
                MAX_EVIDENCE_TEXT_CHARS,
            )
            snippet = self._candidate_snippet(
                matched_texts=matched_texts,
                context_text=context_text,
                keyword=keyword,
            )
            score = self._rule_score(
                keyword=keyword,
                matched_texts=matched_texts,
                context_text=context_text,
                hit_count=len(ordered_hits),
                has_bbox=cluster.bbox is not None,
            )
            candidate_id = f"{pdf_id}:p{cluster.page_index}:r{cluster_index}"
            candidates.append(
                CircuitBodyBestHit(
                    hit_id=representative.hit_id,
                    candidate_id=candidate_id,
                    page_index=cluster.page_index,
                    page_number=cluster.page_index + 1,
                    matched_text=matched_texts[0] if matched_texts else keyword,
                    snippet=snippet,
                    context=context_text[:MAX_EVIDENCE_TEXT_CHARS],
                    nearby_ocr_text=evidence_text[:MAX_EVIDENCE_TEXT_CHARS],
                    highlight_boxes_px=[
                        [float(part) for part in hit.bbox]
                        for hit in ordered_hits
                        if hit.bbox is not None
                    ],
                    source_hit_ids=[hit.hit_id for hit in ordered_hits],
                    display_rank=cluster_index,
                    score=score,
                    confidence=self._confidence(score),
                    reason=self._rule_reason(
                        keyword=keyword,
                        matched_texts=matched_texts,
                        hit_count=len(ordered_hits),
                        document_title=document_title,
                    ),
                )
            )
        return candidates

    def _cluster_hits(self, hits: list[_NormalizedHit]) -> list[_RegionCluster]:
        clusters: list[_RegionCluster] = []
        hits_by_page: dict[int, list[_NormalizedHit]] = defaultdict(list)
        for hit in hits:
            hits_by_page[hit.page_index].append(hit)

        for page_index in sorted(hits_by_page):
            page_hits = sorted(
                hits_by_page[page_index],
                key=lambda hit: (
                    self._box_center(hit.bbox)[1] if hit.bbox else float("inf"),
                    self._box_center(hit.bbox)[0] if hit.bbox else float("inf"),
                    hit.reading_order,
                    hit.element_index,
                ),
            )
            page_clusters: list[_RegionCluster] = []
            for hit in page_hits:
                target_cluster = self._nearest_cluster(page_clusters, hit)
                if target_cluster is None:
                    target_cluster = _RegionCluster(page_index=page_index)
                    page_clusters.append(target_cluster)
                target_cluster.add(hit)
            clusters.extend(page_clusters)
        return clusters

    def _nearest_cluster(
        self,
        clusters: list[_RegionCluster],
        hit: _NormalizedHit,
    ) -> _RegionCluster | None:
        if hit.bbox is None:
            for cluster in clusters:
                if cluster.bbox is None and abs(cluster.hits[-1].reading_order - hit.reading_order) <= 2:
                    return cluster
            return None

        best: tuple[float, _RegionCluster] | None = None
        for cluster in clusters:
            if cluster.bbox is None:
                continue
            distance = self._box_distance(cluster.bbox, hit.bbox)
            threshold = self._cluster_threshold(cluster.bbox, hit.bbox)
            if distance <= threshold and (best is None or distance < best[0]):
                best = (distance, cluster)
        return best[1] if best else None

    def _nearby_ocr_text(
        self,
        *,
        page_elements: list[_OcrElement],
        region_bbox: tuple[float, float, float, float] | None,
        representative: _NormalizedHit,
    ) -> str:
        if not page_elements:
            return representative.context or representative.matched_text

        if region_bbox is None:
            nearby = [
                element
                for element in page_elements
                if abs(element.reading_order - representative.reading_order) <= 12
            ]
        else:
            expanded = self._expand_box(region_bbox, margin=max(900.0, self._box_size(region_bbox) * 2.4))
            nearby = [
                element
                for element in page_elements
                if element.bbox is not None and self._box_intersects(expanded, element.bbox)
            ]
            if not nearby:
                center = self._box_center(region_bbox)
                nearby = sorted(
                    [element for element in page_elements if element.bbox is not None],
                    key=lambda element: self._point_distance(center, self._box_center(element.bbox)),
                )[:16]

        nearby = sorted(nearby, key=lambda element: (element.reading_order, element.text))
        return self._join_limited([element.text for element in nearby if element.text], MAX_EVIDENCE_TEXT_CHARS)

    def _load_ocr_elements(self, latest_result_path: str) -> dict[int, list[_OcrElement]]:
        result_path = self._resolve_path(latest_result_path)
        if result_path is None:
            return {}
        try:
            parsed = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        pages = self._extract_pages(parsed)
        elements_by_page: dict[int, list[_OcrElement]] = {}
        for fallback_page_index, page in enumerate(pages):
            page_index = self._safe_page_index(page, fallback_page_index)
            page_elements: list[_OcrElement] = []
            raw_elements = page.get("elements") or page.get("text_elements") or page.get("ocr_elements") or []
            if not isinstance(raw_elements, list):
                raw_elements = []
            for fallback_index, element in enumerate(raw_elements):
                if not isinstance(element, dict):
                    continue
                text = str(
                    element.get("text_content")
                    or element.get("text")
                    or element.get("content")
                    or element.get("matched_text")
                    or ""
                ).strip()
                if not text:
                    continue
                page_elements.append(
                    _OcrElement(
                        text=text,
                        page_index=page_index,
                        reading_order=self._int_or(element.get("reading_order"), fallback_index),
                        bbox=self._coerce_box(element.get("bounding_box") or element.get("bbox") or element.get("box_px")),
                    )
                )
            elements_by_page[page_index] = page_elements
        return elements_by_page

    def _fallback_best_hit(
        self,
        hits: list[_NormalizedHit],
        *,
        keyword: str,
        pdf_id: str,
    ) -> CircuitBodyBestHit:
        grouped: dict[int, list[_NormalizedHit]] = defaultdict(list)
        for hit in hits:
            grouped[hit.page_index].append(hit)
        best_page_index, page_hits = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[0]
        representative = sorted(
            page_hits,
            key=lambda hit: (hit.reading_order, hit.element_index, hit.char_start, hit.hit_id),
        )[0]
        context_text = representative.context or representative.matched_text
        boxes = [[float(part) for part in representative.bbox]] if representative.bbox is not None else []
        return CircuitBodyBestHit(
            hit_id=representative.hit_id,
            candidate_id=f"{pdf_id}:p{best_page_index}:fallback",
            page_index=best_page_index,
            page_number=best_page_index + 1,
            matched_text=representative.matched_text or keyword,
            snippet=self._candidate_snippet(
                matched_texts=[representative.matched_text],
                context_text=context_text,
                keyword=keyword,
            ),
            context=context_text,
            nearby_ocr_text=context_text,
            highlight_boxes_px=boxes,
            source_hit_ids=[representative.hit_id],
            display_rank=1,
            score=self._rule_score(
                keyword=keyword,
                matched_texts=[representative.matched_text],
                context_text=context_text,
                hit_count=len(page_hits),
                has_bbox=representative.bbox is not None,
            ),
            confidence="medium",
            reason="规则兜底选择命中页中阅读顺序靠前的位置",
        )

    @staticmethod
    def _page_index(hit: dict[str, Any]) -> int | None:
        value = hit.get("page_index")
        try:
            page_index = int(value)
        except (TypeError, ValueError):
            return None
        if page_index < 0:
            return None
        return page_index

    @classmethod
    def _box(cls, hit: dict[str, Any]) -> tuple[float, float, float, float] | None:
        return cls._coerce_box(hit.get("bounding_box") or hit.get("bbox") or hit.get("box_px"))

    @staticmethod
    def _coerce_box(value: Any) -> tuple[float, float, float, float] | None:
        if isinstance(value, dict):
            keys = ("x_min", "y_min", "x_max", "y_max")
            try:
                x1, y1, x2, y2 = (float(value[key]) for key in keys)
            except (KeyError, TypeError, ValueError):
                return None
        elif isinstance(value, (list, tuple)) and len(value) == 4:
            try:
                x1, y1, x2, y2 = (float(part) for part in value)
            except (TypeError, ValueError):
                return None
        else:
            return None
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom

    @staticmethod
    def _int_or(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _context(hit: dict[str, Any]) -> str:
        matched_text = str(hit.get("matched_text") or "").strip()
        context = hit.get("context")
        if isinstance(context, str):
            context_text = context.strip()
        elif isinstance(context, list):
            context_text = " ".join(str(item).strip() for item in context if str(item).strip())
        elif isinstance(context, dict):
            context_text = " ".join(str(item).strip() for item in context.values() if str(item).strip())
        else:
            context_text = ""

        if matched_text and matched_text not in context_text:
            return f"{matched_text} {context_text}".strip()
        return context_text or matched_text

    @staticmethod
    def _candidate_snippet(*, matched_texts: list[str], context_text: str, keyword: str) -> str:
        lead = " ".join(text for text in matched_texts if text).strip() or str(keyword or "").strip()
        snippet = context_text.strip() or lead
        if lead and lead not in snippet:
            snippet = f"{lead} {snippet}".strip()
        return snippet[:240]

    @staticmethod
    def _unique_texts(values: Any) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            unique.append(text)
            seen.add(text)
        return unique

    @staticmethod
    def _join_limited(values: list[str], limit: int) -> str:
        chunks: list[str] = []
        length = 0
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            next_length = length + len(text) + (1 if chunks else 0)
            if next_length > limit:
                remaining = max(limit - length - (1 if chunks else 0), 0)
                if remaining > 0:
                    chunks.append(text[:remaining])
                break
            chunks.append(text)
            length = next_length
        return " ".join(chunks)

    @staticmethod
    def _rule_score(
        *,
        keyword: str,
        matched_texts: list[str],
        context_text: str,
        hit_count: int,
        has_bbox: bool,
    ) -> float:
        query = _compact_text(keyword)
        matched_compact = [_compact_text(text) for text in matched_texts]
        context_compact = _compact_text(context_text)
        exact = 0.0
        if query:
            if any(text == query for text in matched_compact):
                exact = 24.0
            elif any(query in text or text in query for text in matched_compact if text):
                exact = 18.0
            elif query in context_compact:
                exact = 10.0
        return exact + min(max(hit_count, 1), 8) * 4.0 + (2.0 if has_bbox else 0.0)

    @staticmethod
    def _confidence(score: float) -> str:
        if score >= 28:
            return "high"
        if score >= 16:
            return "medium"
        return "low"

    @staticmethod
    def _rule_reason(
        *,
        keyword: str,
        matched_texts: list[str],
        hit_count: int,
        document_title: str,
    ) -> str:
        matched = "、".join(matched_texts[:3]) or keyword
        if hit_count > 1:
            return f"该区域有 {hit_count} 个相邻命中，包含“{matched}”"
        if document_title:
            return f"在《{document_title}》中命中“{matched}”"
        return f"命中“{matched}”"

    @staticmethod
    def _box_center(box: tuple[float, float, float, float] | None) -> tuple[float, float]:
        if box is None:
            return 0.0, 0.0
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    @classmethod
    def _box_distance(
        cls,
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> float:
        if cls._box_intersects(left, right):
            return 0.0
        left_center = cls._box_center(left)
        right_center = cls._box_center(right)
        return cls._point_distance(left_center, right_center)

    @staticmethod
    def _point_distance(left: tuple[float, float], right: tuple[float, float]) -> float:
        return math.hypot(left[0] - right[0], left[1] - right[1])

    @staticmethod
    def _box_size(box: tuple[float, float, float, float]) -> float:
        return max(box[2] - box[0], box[3] - box[1], 1.0)

    @classmethod
    def _cluster_threshold(
        cls,
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> float:
        return max(360.0, min(cls._box_size(left), cls._box_size(right)) * 4.0)

    @staticmethod
    def _expand_box(
        box: tuple[float, float, float, float],
        *,
        margin: float,
    ) -> tuple[float, float, float, float]:
        return box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin

    @staticmethod
    def _box_intersects(
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> bool:
        return not (left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1])

    @staticmethod
    def _resolve_path(value: str) -> Path | None:
        raw = str(value or "").strip()
        if not raw or raw.startswith(("http://", "https://")):
            return None
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None
        for base in (Path.cwd(), BACKEND_DIR, PROJECT_ROOT):
            resolved = base / candidate
            if resolved.exists():
                return resolved
        return None

    @staticmethod
    def _extract_pages(parsed: Any) -> list[dict[str, Any]]:
        pages: list[Any] = []
        if isinstance(parsed, dict):
            for key in ("pages", "page_results", "results"):
                value = parsed.get(key)
                if isinstance(value, list):
                    pages = value
                    break
        elif isinstance(parsed, list):
            pages = parsed
        return [page for page in pages if isinstance(page, dict)]

    @staticmethod
    def _safe_page_index(page: dict[str, Any], fallback: int) -> int:
        try:
            value = int(page.get("page_index", fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(value, 0)


def _merge_optional_boxes(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if left is None:
        return right
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )


def _compact_text(value: str) -> str:
    return "".join(str(value or "").lower().split())
