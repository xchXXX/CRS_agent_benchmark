"""Build PDF-loader point parameters for the WebView circuit viewer."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
from app.agent.domain.circuit_body_search.preview_token import CircuitBodyPreviewTokenPayload
from app.core.config import BACKEND_DIR, PROJECT_ROOT


logger = logging.getLogger(__name__)

DEFAULT_OCR_DPI = 600.0


@dataclass(frozen=True)
class CircuitViewerPageSize:
    width: float
    height: float


class CircuitBodyViewerPointLocator:
    """Convert parser pixel boxes into normalized PDF-loader points."""

    def __init__(self, *, config_provider: CircuitBodySearchConfigProvider | None = None) -> None:
        self._config_provider = config_provider or CircuitBodySearchConfigProvider()
        self._result_page_size_cache: dict[str, dict[int, CircuitViewerPageSize]] = {}
        self._pdf_page_size_cache: dict[str, dict[int, CircuitViewerPageSize]] = {}

    def points_for_bbox(
        self,
        *,
        payload: CircuitBodyPreviewTokenPayload,
        page_index: int,
        bbox: list[float] | tuple[float, float, float, float],
        raw_hit: dict[str, Any] | None = None,
    ) -> str:
        box = _coerce_bbox(bbox)
        if box is None:
            return ""
        if _is_normalized_bbox(box):
            return _format_points(box)

        page_size = self._page_size_from_hit(raw_hit) if raw_hit is not None else None
        if page_size is None:
            page_size = self.page_size(payload=payload, page_index=page_index)
        if page_size is None or page_size.width <= 0 or page_size.height <= 0:
            return ""

        normalized = (
            _clamp(box[0] / page_size.width),
            _clamp(box[1] / page_size.height),
            _clamp(box[2] / page_size.width),
            _clamp(box[3] / page_size.height),
        )
        normalized_box = _coerce_bbox(normalized)
        return _format_points(normalized_box) if normalized_box is not None else ""

    def page_size(
        self,
        *,
        payload: CircuitBodyPreviewTokenPayload,
        page_index: int,
    ) -> CircuitViewerPageSize | None:
        from_result = self._page_size_from_result_json(payload.latest_result_path, page_index)
        if from_result is not None:
            return from_result
        return self._page_size_from_pdf(payload.source_pdf_url, page_index)

    def _page_size_from_hit(self, raw_hit: dict[str, Any]) -> CircuitViewerPageSize | None:
        for width_key, height_key in (
            ("page_width_px", "page_height_px"),
            ("rendered_width_px", "rendered_height_px"),
            ("image_width_px", "image_height_px"),
            ("width_px", "height_px"),
            ("page_width", "page_height"),
        ):
            size = _page_size_from_mapping(raw_hit, width_key, height_key)
            if size is not None:
                return size

        metadata = raw_hit.get("page_metadata") or raw_hit.get("metadata")
        if isinstance(metadata, dict):
            return self._page_size_from_hit(metadata)
        return None

    def _page_size_from_result_json(self, result_path_value: str, page_index: int) -> CircuitViewerPageSize | None:
        path = self._resolve_result_path(result_path_value)
        if path is None:
            return None

        cache_key = str(path)
        cached = self._result_page_size_cache.get(cache_key)
        if cached is None:
            cached = self._read_result_page_sizes(path)
            self._result_page_size_cache[cache_key] = cached
        return cached.get(page_index)

    def _read_result_page_sizes(self, path: Path) -> dict[int, CircuitViewerPageSize]:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.info("Circuit viewer result JSON unavailable for points: %s", exc)
            return {}

        sizes: dict[int, CircuitViewerPageSize] = {}
        for fallback_index, page in enumerate(_extract_pages(parsed)):
            page_index = _safe_page_index(page, fallback_index)
            metadata = page.get("page_metadata") or page.get("metadata")
            size = _page_size_from_page(page)
            if size is None and isinstance(metadata, dict):
                size = _page_size_from_page(metadata)
            if size is not None:
                sizes[page_index] = size
        return sizes

    def _page_size_from_pdf(self, source_pdf_url: str, page_index: int) -> CircuitViewerPageSize | None:
        pdf_url = _extract_pdf_url(source_pdf_url)
        if not pdf_url:
            return None

        cached = self._pdf_page_size_cache.get(pdf_url)
        if cached is None:
            cached = self._read_pdf_page_sizes(pdf_url)
            self._pdf_page_size_cache[pdf_url] = cached
        return cached.get(page_index)

    def _read_pdf_page_sizes(self, pdf_url: str) -> dict[int, CircuitViewerPageSize]:
        try:
            import fitz  # type: ignore[import-not-found]
        except Exception as exc:
            logger.info("Circuit viewer PDF sizing unavailable: %s", exc)
            return {}

        config = self._config_provider.load()
        document = None
        try:
            response = httpx.get(pdf_url, timeout=config.preview_pdf_timeout)
            response.raise_for_status()
            document = fitz.open(stream=response.content, filetype="pdf")
            sizes: dict[int, CircuitViewerPageSize] = {}
            for index in range(len(document)):
                page = document.load_page(index)
                sizes[index] = CircuitViewerPageSize(
                    width=float(page.rect.width) * DEFAULT_OCR_DPI / 72.0,
                    height=float(page.rect.height) * DEFAULT_OCR_DPI / 72.0,
                )
            return sizes
        except Exception as exc:
            logger.info("Circuit viewer PDF sizing failed: %s", exc)
            return {}
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    pass

    def _resolve_result_path(self, value: str) -> Path | None:
        raw = str(value or "").strip()
        if not raw or _is_http_url(raw):
            return None

        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None

        config = self._config_provider.load()
        bases: list[Path] = []
        configured_base = str(config.preview_result_base_dir or "").strip()
        if configured_base:
            bases.append(Path(configured_base))
        bases.extend([Path.cwd(), BACKEND_DIR, PROJECT_ROOT])

        for base in bases:
            resolved = base / candidate
            if resolved.exists():
                return resolved
        return None


def _extract_pages(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if not isinstance(parsed, dict):
        return []
    for key in ("pages", "page_results", "results"):
        value = parsed.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _safe_page_index(page: dict[str, Any], fallback: int) -> int:
    try:
        page_index = int(page.get("page_index", fallback))
    except (TypeError, ValueError):
        return fallback
    return max(page_index, 0)


def _page_size_from_page(page: dict[str, Any]) -> CircuitViewerPageSize | None:
    for width_key, height_key in (
        ("rendered_width_px", "rendered_height_px"),
        ("image_width_px", "image_height_px"),
        ("width_px", "height_px"),
        ("page_width_px", "page_height_px"),
        ("width", "height"),
    ):
        size = _page_size_from_mapping(page, width_key, height_key)
        if size is not None:
            return size
    return None


def _page_size_from_mapping(value: dict[str, Any], width_key: str, height_key: str) -> CircuitViewerPageSize | None:
    try:
        width = float(value.get(width_key) or 0)
        height = float(value.get(height_key) or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return CircuitViewerPageSize(width=width, height=height)


def _extract_pdf_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _looks_like_pdf_url(raw):
        return raw

    parsed = urlparse(raw)
    fragments = [parsed.fragment, parsed.query]
    for fragment in fragments:
        if not fragment:
            continue
        query = fragment
        if "?" in query:
            query = query.split("?", 1)[1]
        params = parse_qs(query)
        for key in ("file", "url", "pdf", "src"):
            for candidate in params.get(key, []):
                decoded = unquote(str(candidate or "").strip())
                if _looks_like_pdf_url(decoded):
                    return decoded
    return ""


def _looks_like_pdf_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return parsed.path.lower().endswith(".pdf")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _coerce_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _is_normalized_bbox(value: tuple[float, float, float, float]) -> bool:
    return all(0.0 <= part <= 1.0 for part in value)


def _format_points(value: tuple[float, float, float, float]) -> str:
    return ",".join(_format_point(part) for part in value)


def _format_point(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)
