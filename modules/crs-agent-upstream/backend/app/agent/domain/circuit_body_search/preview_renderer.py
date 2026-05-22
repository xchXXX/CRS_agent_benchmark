"""Render cropped preview images for circuit body-search hits."""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
from app.agent.domain.circuit_body_search.preview_token import CircuitBodyPreviewTokenPayload
from app.core.config import BACKEND_DIR, PROJECT_ROOT


logger = logging.getLogger(__name__)

DEFAULT_SOURCE_DPI = 600.0
MAX_RENDER_DPI = 300.0
MAX_OUTPUT_DIMENSION_PX = 1400
MIN_CROP_MARGIN_PX = 720.0
CROP_MARGIN_RATIO = 1.25
PAGE_IMAGE_CACHE_MAX_ITEMS = 48
METADATA_CACHE_MAX_ITEMS = 96
PDF_BYTES_CACHE_MAX_ITEMS = 4
PDF_BYTES_CACHE_MAX_TOTAL = 96 * 1024 * 1024


class CircuitBodyPreviewRenderError(RuntimeError):
    """Raised when a preview image cannot be rendered."""


class CircuitBodyPreviewRenderer:
    """Build a cropped PNG from parser page images or the source PDF."""

    def __init__(self, *, config_provider: CircuitBodySearchConfigProvider | None = None) -> None:
        self._config_provider = config_provider or CircuitBodySearchConfigProvider()
        self._cache_lock = RLock()
        self._page_image_cache: OrderedDict[tuple[Any, ...], tuple[bytes, str]] = OrderedDict()
        self._metadata_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._pdf_bytes_cache: OrderedDict[str, bytes] = OrderedDict()
        self._pdf_bytes_cache_size = 0

    def render(self, payload: CircuitBodyPreviewTokenPayload) -> tuple[bytes, str]:
        boxes = self._normalize_boxes(payload.highlight_boxes_px)
        if payload.page_index < 0 or not boxes:
            raise CircuitBodyPreviewRenderError("invalid_preview_target")

        result_path, page_metadata = self._load_page_metadata(payload)
        try:
            image = self._render_from_parser_image(result_path, page_metadata, boxes)
            if image is not None:
                return self._encode_png(image), "image/png"
        except Exception as exc:
            logger.info("Circuit preview parser-image render skipped: %s", exc)

        image = self._render_from_pdf(payload, page_metadata, boxes)
        return self._encode_png(image), "image/png"

    def metadata(self, payload: CircuitBodyPreviewTokenPayload) -> dict[str, Any]:
        """Return viewer metadata without exposing parser paths to the browser."""
        cache_key = self._metadata_cache_key(payload)
        cached = self._get_lru(self._metadata_cache, cache_key)
        if cached is not None:
            return deepcopy(cached)

        result_path, pages = self._load_pages(payload.latest_result_path)
        page_summaries: list[dict[str, Any]] = []
        for fallback_index, page in enumerate(pages):
            page_index = self._safe_page_index(page, fallback_index)
            page_metadata = page.get("page_metadata") or page.get("metadata") or {}
            if not isinstance(page_metadata, dict):
                page_metadata = {}
            width, height = self._metadata_dimensions(page_metadata)
            page_summaries.append(
                {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "width_px": width,
                    "height_px": height,
                }
            )

        total_pages = len(page_summaries)
        if total_pages <= 0:
            pdf_total_pages, pdf_page_metadata = self._pdf_document_info(payload, payload.page_index)
            total_pages = pdf_total_pages
            width, height = self._metadata_dimensions(pdf_page_metadata)
            if width > 0 and height > 0:
                page_summaries.append(
                    {
                        "page_index": payload.page_index,
                        "page_number": payload.page_index + 1,
                        "width_px": width,
                        "height_px": height,
                    }
                )

        initial_page_index = max(int(payload.page_index), 0)
        if total_pages > 0:
            initial_page_index = min(initial_page_index, total_pages - 1)

        metadata = {
            "pdf_id": payload.pdf_id,
            "filename": payload.filename,
            "keyword": payload.keyword,
            "initial_hit_id": payload.hit_id,
            "initial_page_index": initial_page_index,
            "initial_page_number": initial_page_index + 1,
            "initial_highlight_boxes_px": payload.highlight_boxes_px,
            "total_pages": total_pages,
            "pages": page_summaries,
            "has_result_json": result_path is not None and bool(pages),
            "has_source_pdf_url": bool(payload.source_pdf_url),
        }
        self._set_lru(self._metadata_cache, cache_key, deepcopy(metadata), METADATA_CACHE_MAX_ITEMS)
        return metadata

    def render_page(
        self,
        payload: CircuitBodyPreviewTokenPayload,
        *,
        page_index: int,
    ) -> tuple[bytes, str]:
        """Render a full page for the in-document viewer."""
        if page_index < 0:
            raise CircuitBodyPreviewRenderError("invalid_page_index")

        cache_key = self._page_image_cache_key(payload, page_index)
        cached = self._get_lru(self._page_image_cache, cache_key)
        if cached is not None:
            return cached

        result_path, page_metadata = self._load_page_metadata_for_index(payload, page_index)
        try:
            image = self._render_full_page_from_parser_image(result_path, page_metadata)
            if image is not None:
                rendered = (self._encode_png(self._resize_for_response(image)), "image/png")
                self._set_lru(self._page_image_cache, cache_key, rendered, PAGE_IMAGE_CACHE_MAX_ITEMS)
                return rendered
        except Exception as exc:
            logger.info("Circuit viewer parser-image render skipped: %s", exc)

        image = self._render_full_page_from_pdf(payload, page_metadata, page_index=page_index)
        rendered = (self._encode_png(self._resize_for_response(image)), "image/png")
        self._set_lru(self._page_image_cache, cache_key, rendered, PAGE_IMAGE_CACHE_MAX_ITEMS)
        return rendered

    def _metadata_cache_key(self, payload: CircuitBodyPreviewTokenPayload) -> tuple[Any, ...]:
        return (
            "metadata",
            payload.pdf_id,
            payload.filename,
            payload.keyword,
            payload.hit_id,
            payload.latest_result_path,
            payload.source_pdf_url,
            int(payload.page_index),
            self._boxes_cache_key(payload.highlight_boxes_px),
        )

    def _page_image_cache_key(self, payload: CircuitBodyPreviewTokenPayload, page_index: int) -> tuple[Any, ...]:
        return (
            "page_image",
            payload.pdf_id,
            payload.latest_result_path,
            payload.source_pdf_url,
            int(page_index),
        )

    @staticmethod
    def _boxes_cache_key(boxes: list[list[float]]) -> tuple[tuple[float, float, float, float], ...]:
        normalized: list[tuple[float, float, float, float]] = []
        for box in boxes or []:
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                left, top, right, bottom = (round(float(part), 3) for part in box)
            except (TypeError, ValueError):
                continue
            normalized.append((left, top, right, bottom))
        return tuple(normalized)

    def _get_lru(self, cache: OrderedDict[Any, Any], key: Any) -> Any | None:
        with self._cache_lock:
            try:
                value = cache.pop(key)
            except KeyError:
                return None
            cache[key] = value
            return value

    def _set_lru(self, cache: OrderedDict[Any, Any], key: Any, value: Any, max_items: int) -> None:
        with self._cache_lock:
            cache.pop(key, None)
            cache[key] = value
            while len(cache) > max_items:
                cache.popitem(last=False)

    def _get_pdf_bytes(self, url: str, *, timeout: float) -> bytes:
        cached = self._get_lru(self._pdf_bytes_cache, url)
        if cached is not None:
            return cached

        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        content = bytes(response.content)
        self._set_pdf_bytes_cache(url, content)
        return content

    def _set_pdf_bytes_cache(self, url: str, content: bytes) -> None:
        if len(content) > PDF_BYTES_CACHE_MAX_TOTAL:
            return

        with self._cache_lock:
            previous = self._pdf_bytes_cache.pop(url, None)
            if previous is not None:
                self._pdf_bytes_cache_size -= len(previous)
            self._pdf_bytes_cache[url] = content
            self._pdf_bytes_cache_size += len(content)

            while (
                len(self._pdf_bytes_cache) > PDF_BYTES_CACHE_MAX_ITEMS
                or self._pdf_bytes_cache_size > PDF_BYTES_CACHE_MAX_TOTAL
            ):
                _, removed = self._pdf_bytes_cache.popitem(last=False)
                self._pdf_bytes_cache_size -= len(removed)

    def _load_page_metadata(self, payload: CircuitBodyPreviewTokenPayload) -> tuple[Path | None, dict[str, Any]]:
        return self._load_page_metadata_for_index(payload, payload.page_index)

    def _load_page_metadata_for_index(
        self,
        payload: CircuitBodyPreviewTokenPayload,
        page_index: int,
    ) -> tuple[Path | None, dict[str, Any]]:
        result_path, pages = self._load_pages(payload.latest_result_path)
        page = self._find_page_in_pages(pages, page_index)
        if not page:
            return result_path, {}
        metadata = page.get("page_metadata") or page.get("metadata") or {}
        return result_path, metadata if isinstance(metadata, dict) else {}

    def _load_pages(self, result_path_value: str) -> tuple[Path | None, list[dict[str, Any]]]:
        result_path = self._resolve_path(result_path_value)
        if result_path is None:
            return None, []

        try:
            with result_path.open("r", encoding="utf-8") as file:
                parsed = json.load(file)
        except Exception as exc:
            logger.info("Circuit preview result JSON unavailable: %s", exc)
            return result_path, []

        return result_path, self._extract_pages(parsed)

    def _render_from_parser_image(
        self,
        result_path: Path | None,
        page_metadata: dict[str, Any],
        boxes: list[tuple[float, float, float, float]],
    ) -> Image.Image | None:
        if not page_metadata:
            return None

        image_ref = str(page_metadata.get("image_path") or "").strip()
        image_filename = str(page_metadata.get("image_filename") or "").strip()
        image = self._open_image(image_ref, result_path=result_path)
        if image is None and image_filename:
            image = self._open_image(image_filename, result_path=result_path)
        if image is None:
            return None

        return self._crop_and_highlight(image, boxes)

    def _render_full_page_from_parser_image(
        self,
        result_path: Path | None,
        page_metadata: dict[str, Any],
    ) -> Image.Image | None:
        if not page_metadata:
            return None

        image_ref = str(page_metadata.get("image_path") or "").strip()
        image_filename = str(page_metadata.get("image_filename") or "").strip()
        image = self._open_image(image_ref, result_path=result_path)
        if image is None and image_filename:
            image = self._open_image(image_filename, result_path=result_path)
        return image

    def _render_from_pdf(
        self,
        payload: CircuitBodyPreviewTokenPayload,
        page_metadata: dict[str, Any],
        boxes: list[tuple[float, float, float, float]],
    ) -> Image.Image:
        if not payload.source_pdf_url:
            raise CircuitBodyPreviewRenderError("preview_source_unavailable")

        try:
            import fitz  # type: ignore[import-not-found]
        except Exception as exc:
            raise CircuitBodyPreviewRenderError("pdf_renderer_unavailable") from exc

        config = self._config_provider.load()
        try:
            pdf_bytes = self._get_pdf_bytes(payload.source_pdf_url, timeout=config.preview_pdf_timeout)
        except Exception as exc:
            raise CircuitBodyPreviewRenderError("source_pdf_unavailable") from exc

        source_dpi = self._metadata_dpi(page_metadata)
        render_dpi = min(source_dpi, MAX_RENDER_DPI)

        document = None
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            if payload.page_index >= len(document):
                raise CircuitBodyPreviewRenderError("preview_page_out_of_range")
            page = document.load_page(payload.page_index)
            page_width_px, page_height_px = self._page_dimensions_px(page_metadata, page, source_dpi)
            bounds = self._crop_bounds(boxes, page_width_px, page_height_px)
            clip = fitz.Rect(
                bounds[0] / source_dpi * 72,
                bounds[1] / source_dpi * 72,
                bounds[2] / source_dpi * 72,
                bounds[3] / source_dpi * 72,
            )
            matrix = fitz.Matrix(render_dpi / 72, render_dpi / 72)
            pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    pass

        scale_x = image.width / max(bounds[2] - bounds[0], 1.0)
        scale_y = image.height / max(bounds[3] - bounds[1], 1.0)
        self._draw_highlights(image, boxes, bounds, scale_x=scale_x, scale_y=scale_y)
        return self._resize_for_response(image)

    def _render_full_page_from_pdf(
        self,
        payload: CircuitBodyPreviewTokenPayload,
        page_metadata: dict[str, Any],
        *,
        page_index: int,
    ) -> Image.Image:
        if not payload.source_pdf_url:
            raise CircuitBodyPreviewRenderError("preview_source_unavailable")

        try:
            import fitz  # type: ignore[import-not-found]
        except Exception as exc:
            raise CircuitBodyPreviewRenderError("pdf_renderer_unavailable") from exc

        config = self._config_provider.load()
        try:
            pdf_bytes = self._get_pdf_bytes(payload.source_pdf_url, timeout=config.preview_pdf_timeout)
        except Exception as exc:
            raise CircuitBodyPreviewRenderError("source_pdf_unavailable") from exc

        source_dpi = self._metadata_dpi(page_metadata)
        render_dpi = min(source_dpi, MAX_RENDER_DPI)

        document = None
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            if page_index >= len(document):
                raise CircuitBodyPreviewRenderError("preview_page_out_of_range")
            page = document.load_page(page_index)
            matrix = fitz.Matrix(render_dpi / 72, render_dpi / 72)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    pass

    def _pdf_document_info(
        self,
        payload: CircuitBodyPreviewTokenPayload,
        page_index: int,
    ) -> tuple[int, dict[str, Any]]:
        if not payload.source_pdf_url:
            return 0, {}

        try:
            import fitz  # type: ignore[import-not-found]
        except Exception:
            return 0, {}

        config = self._config_provider.load()
        document = None
        try:
            pdf_bytes = self._get_pdf_bytes(payload.source_pdf_url, timeout=config.preview_pdf_timeout)
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(document) <= 0:
                return 0, {}
            safe_index = min(max(page_index, 0), len(document) - 1)
            page = document.load_page(safe_index)
            return len(document), {
                "rendered_width_px": float(page.rect.width) * DEFAULT_SOURCE_DPI / 72,
                "rendered_height_px": float(page.rect.height) * DEFAULT_SOURCE_DPI / 72,
                "dpi": DEFAULT_SOURCE_DPI,
            }
        except Exception as exc:
            logger.info("Circuit viewer PDF metadata unavailable: %s", exc)
            return 0, {}
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    pass

    def _open_image(self, value: str, *, result_path: Path | None) -> Image.Image | None:
        if not value:
            return None
        if self._is_http_url(value):
            config = self._config_provider.load()
            response = httpx.get(value, timeout=config.preview_pdf_timeout)
            response.raise_for_status()
            return Image.open(BytesIO(response.content)).convert("RGB")

        image_path = self._resolve_path(value, anchor=result_path.parent if result_path else None)
        if image_path is None:
            return None
        return Image.open(image_path).convert("RGB")

    def _resolve_path(self, value: str, *, anchor: Path | None = None) -> Path | None:
        raw = str(value or "").strip()
        if not raw or self._is_http_url(raw):
            return None

        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None

        config = self._config_provider.load()
        bases: list[Path] = []
        configured_base = str(config.preview_result_base_dir or "").strip()
        if configured_base:
            bases.append(Path(configured_base))
        if anchor is not None:
            bases.append(anchor)
        bases.extend([Path.cwd(), BACKEND_DIR, PROJECT_ROOT])

        for base in bases:
            resolved = base / candidate
            if resolved.exists():
                return resolved
        return None

    @classmethod
    def _find_page(cls, parsed: Any, page_index: int) -> dict[str, Any]:
        return cls._find_page_in_pages(cls._extract_pages(parsed), page_index)

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

    @classmethod
    def _find_page_in_pages(cls, pages: list[dict[str, Any]], page_index: int) -> dict[str, Any]:
        for index, page in enumerate(pages):
            if cls._safe_page_index(page, index) == page_index:
                return page
        return {}

    @staticmethod
    def _safe_page_index(page: dict[str, Any], fallback: int) -> int:
        try:
            value = int(page.get("page_index", fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(value, 0)

    @staticmethod
    def _metadata_dimensions(page_metadata: dict[str, Any]) -> tuple[float, float]:
        width = 0.0
        height = 0.0
        for width_key, height_key in (
            ("rendered_width_px", "rendered_height_px"),
            ("image_width_px", "image_height_px"),
            ("width_px", "height_px"),
        ):
            try:
                width = float(page_metadata.get(width_key) or 0)
                height = float(page_metadata.get(height_key) or 0)
            except (TypeError, ValueError):
                width = 0.0
                height = 0.0
            if width > 0 and height > 0:
                return width, height
        return 0.0, 0.0

    def _crop_and_highlight(
        self,
        image: Image.Image,
        boxes: list[tuple[float, float, float, float]],
    ) -> Image.Image:
        bounds = self._crop_bounds(boxes, float(image.width), float(image.height))
        crop_box = tuple(int(round(value)) for value in bounds)
        cropped = image.crop(crop_box)
        self._draw_highlights(cropped, boxes, bounds)
        return self._resize_for_response(cropped)

    @staticmethod
    def _normalize_boxes(value: list[list[float]]) -> list[tuple[float, float, float, float]]:
        boxes: list[tuple[float, float, float, float]] = []
        for item in value or []:
            if not isinstance(item, (list, tuple)) or len(item) != 4:
                continue
            try:
                x1, y1, x2, y2 = (float(part) for part in item)
            except (TypeError, ValueError):
                continue
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right > left and bottom > top:
                boxes.append((left, top, right, bottom))
        return boxes

    @staticmethod
    def _crop_bounds(
        boxes: list[tuple[float, float, float, float]],
        width: float,
        height: float,
    ) -> tuple[float, float, float, float]:
        min_x = min(box[0] for box in boxes)
        min_y = min(box[1] for box in boxes)
        max_x = max(box[2] for box in boxes)
        max_y = max(box[3] for box in boxes)

        hit_width = max(max_x - min_x, 1.0)
        hit_height = max(max_y - min_y, 1.0)
        margin = max(hit_width * CROP_MARGIN_RATIO, hit_height * CROP_MARGIN_RATIO, MIN_CROP_MARGIN_PX)
        left = max(0.0, min_x - margin)
        top = max(0.0, min_y - margin)
        right = min(width, max_x + margin)
        bottom = min(height, max_y + margin)
        if right <= left or bottom <= top:
            raise CircuitBodyPreviewRenderError("invalid_preview_crop")
        return left, top, right, bottom

    @staticmethod
    def _draw_highlights(
        image: Image.Image,
        boxes: list[tuple[float, float, float, float]],
        bounds: tuple[float, float, float, float],
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> None:
        draw = ImageDraw.Draw(image)
        line_width = max(3, int(round(min(image.width, image.height) * 0.008)))
        for left, top, right, bottom in boxes:
            draw.rectangle(
                [
                    (left - bounds[0]) * scale_x,
                    (top - bounds[1]) * scale_y,
                    (right - bounds[0]) * scale_x,
                    (bottom - bounds[1]) * scale_y,
                ],
                outline=(230, 76, 60),
                width=line_width,
            )

    @staticmethod
    def _resize_for_response(image: Image.Image) -> Image.Image:
        max_dimension = max(image.width, image.height)
        if max_dimension <= MAX_OUTPUT_DIMENSION_PX:
            return image
        scale = MAX_OUTPUT_DIMENSION_PX / max_dimension
        target_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        return image.resize(target_size, Image.Resampling.LANCZOS)

    @staticmethod
    def _encode_png(image: Image.Image) -> bytes:
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    @staticmethod
    def _metadata_dpi(page_metadata: dict[str, Any]) -> float:
        for key in ("dpi", "effective_dpi"):
            try:
                value = float(page_metadata.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return min(max(value, 72.0), DEFAULT_SOURCE_DPI)
        return DEFAULT_SOURCE_DPI

    @staticmethod
    def _page_dimensions_px(page_metadata: dict[str, Any], page: Any, source_dpi: float) -> tuple[float, float]:
        try:
            width = float(page_metadata.get("rendered_width_px"))
            height = float(page_metadata.get("rendered_height_px"))
        except (TypeError, ValueError):
            width = 0.0
            height = 0.0
        if width > 0 and height > 0:
            return width, height
        return float(page.rect.width) * source_dpi / 72, float(page.rect.height) * source_dpi / 72

    @staticmethod
    def _is_http_url(value: str) -> bool:
        parsed = urlparse(str(value or ""))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
