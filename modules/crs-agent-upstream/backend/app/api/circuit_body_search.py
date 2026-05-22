"""Circuit body-search preview endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
from app.agent.domain.circuit_body_search.preview_renderer import (
    CircuitBodyPreviewRenderError,
    CircuitBodyPreviewRenderer,
)
from app.agent.domain.circuit_body_search.search_client import CircuitBodySearchClient
from app.agent.domain.circuit_body_search.viewer_points import CircuitBodyViewerPointLocator
from app.agent.domain.circuit_body_search.preview_token import (
    CircuitBodyPreviewTokenCodec,
    CircuitBodyPreviewTokenError,
    CircuitBodyPreviewTokenPayload,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["circuit-body-search"])


class CircuitBodyViewerSearchRequest(BaseModel):
    keyword: str = ""
    limit: int = Field(default=200, ge=1, le=500)


def _runtime_deps(raw_request: Request) -> Any:
    return getattr(raw_request.app.state, "runtime_deps", None)


def _codec(deps: Any) -> CircuitBodyPreviewTokenCodec | Any:
    codec = getattr(deps, "circuit_body_preview_token_codec", None) if deps is not None else None
    return codec or CircuitBodyPreviewTokenCodec()


def _renderer(deps: Any) -> CircuitBodyPreviewRenderer | Any:
    renderer = getattr(deps, "circuit_body_preview_renderer", None) if deps is not None else None
    if renderer is not None:
        return renderer
    return CircuitBodyPreviewRenderer(
        config_provider=CircuitBodySearchConfigProvider(
            config_service=getattr(deps, "config_service", None) if deps is not None else None,
        )
    )


def _search_client(deps: Any) -> CircuitBodySearchClient | Any:
    client = getattr(deps, "circuit_body_search_client", None) if deps is not None else None
    if client is not None:
        return client
    return CircuitBodySearchClient(
        config_provider=CircuitBodySearchConfigProvider(
            config_service=getattr(deps, "config_service", None) if deps is not None else None,
        )
    )


def _point_locator(deps: Any) -> CircuitBodyViewerPointLocator | Any:
    locator = getattr(deps, "circuit_body_viewer_point_locator", None) if deps is not None else None
    if locator is not None:
        return locator
    return CircuitBodyViewerPointLocator(
        config_provider=CircuitBodySearchConfigProvider(
            config_service=getattr(deps, "config_service", None) if deps is not None else None,
        )
    )


def _decode_viewer_token(token: str, deps: Any):
    try:
        return _codec(deps).decode(token)
    except CircuitBodyPreviewTokenError as exc:
        raise HTTPException(status_code=404, detail="图内查看链接已失效") from exc


@router.get("/circuit-body-search/preview/{token}")
@router.get("/chat/api/circuit-body-search/preview/{token}")
async def get_circuit_body_preview(token: str, raw_request: Request) -> Response:
    deps = _runtime_deps(raw_request)
    codec = _codec(deps)
    renderer = _renderer(deps)

    try:
        payload = codec.decode(token)
    except CircuitBodyPreviewTokenError as exc:
        raise HTTPException(status_code=404, detail="局部图预览已失效") from exc

    try:
        content, media_type = await asyncio.to_thread(renderer.render, payload)
    except CircuitBodyPreviewRenderError as exc:
        logger.warning("Circuit body preview render failed: %s", exc)
        raise HTTPException(status_code=404, detail="局部图预览暂不可用") from exc
    except Exception as exc:
        logger.exception("Circuit body preview unexpected failure: %s", exc)
        raise HTTPException(status_code=500, detail="局部图预览生成失败") from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/circuit-body-search/viewer/{token}/metadata")
@router.get("/chat/api/circuit-body-search/viewer/{token}/metadata")
async def get_circuit_body_viewer_metadata(token: str, raw_request: Request) -> dict[str, Any]:
    deps = _runtime_deps(raw_request)
    payload = _decode_viewer_token(token, deps)
    renderer = _renderer(deps)
    try:
        return await asyncio.to_thread(renderer.metadata, payload)
    except Exception as exc:
        logger.exception("Circuit body viewer metadata failed: %s", exc)
        raise HTTPException(status_code=500, detail="图内查看信息加载失败") from exc


@router.get("/circuit-body-search/viewer/{token}/page/{page_index}/image")
@router.get("/chat/api/circuit-body-search/viewer/{token}/page/{page_index}/image")
async def get_circuit_body_viewer_page_image(token: str, page_index: int, raw_request: Request) -> Response:
    deps = _runtime_deps(raw_request)
    payload = _decode_viewer_token(token, deps)
    renderer = _renderer(deps)
    try:
        content, media_type = await asyncio.to_thread(renderer.render_page, payload, page_index=page_index)
    except CircuitBodyPreviewRenderError as exc:
        logger.warning("Circuit body viewer page render failed: %s", exc)
        raise HTTPException(status_code=404, detail="图内页图暂不可用") from exc
    except Exception as exc:
        logger.exception("Circuit body viewer page unexpected failure: %s", exc)
        raise HTTPException(status_code=500, detail="图内页图生成失败") from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/circuit-body-search/viewer/{token}/search")
@router.post("/chat/api/circuit-body-search/viewer/{token}/search")
async def search_circuit_body_viewer(
    token: str,
    request: CircuitBodyViewerSearchRequest,
    raw_request: Request,
) -> dict[str, Any]:
    deps = _runtime_deps(raw_request)
    payload = _decode_viewer_token(token, deps)
    keyword = request.keyword.strip()
    if not keyword:
        return {
            "keyword": "",
            "total_matches": 0,
            "positioned_match_count": 0,
            "truncated": False,
            "results": [],
            "page_summary": [],
        }

    client = _search_client(deps)
    raw_response = await client.search(pdf_id=payload.pdf_id, keyword=keyword)
    if isinstance(raw_response, dict) and raw_response.get("status") == "failed":
        raise HTTPException(status_code=502, detail="图内搜索暂不可用")

    data = raw_response.get("data") if isinstance(raw_response, dict) and isinstance(raw_response.get("data"), dict) else raw_response
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="图内搜索响应格式异常")

    raw_results = data.get("results") if isinstance(data.get("results"), list) else []
    positioned_results = await asyncio.to_thread(
        _normalize_viewer_hits,
        raw_results,
        keyword=keyword,
        payload=payload,
        point_locator=_point_locator(deps),
    )
    page_counts: dict[int, int] = {}
    for item in positioned_results:
        page_index = int(item["page_index"])
        page_counts[page_index] = page_counts.get(page_index, 0) + 1

    limited_results = positioned_results[: request.limit]
    return {
        "keyword": keyword,
        "pdf_id": payload.pdf_id,
        "initial_hit_id": payload.hit_id,
        "total_matches": _int_or(data.get("total_matches"), len(positioned_results)),
        "positioned_match_count": len(positioned_results),
        "truncated": len(positioned_results) > len(limited_results),
        "results": limited_results,
        "page_summary": [
            {"page_index": page_index, "page_number": page_index + 1, "match_count": count}
            for page_index, count in sorted(page_counts.items())
        ],
    }


def _normalize_viewer_hits(
    raw_results: list[Any],
    *,
    keyword: str,
    payload: CircuitBodyPreviewTokenPayload | None = None,
    point_locator: CircuitBodyViewerPointLocator | Any | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for fallback_index, raw_hit in enumerate(raw_results):
        if not isinstance(raw_hit, dict):
            continue
        page_index = _page_index(raw_hit)
        bbox = _bbox(raw_hit)
        if page_index is None or bbox is None:
            continue
        reading_order = _int_or(raw_hit.get("reading_order"), fallback_index)
        element_index = _int_or(raw_hit.get("element_index"), fallback_index)
        char_start = _int_or(raw_hit.get("char_start"), 0)
        hit_id = str(raw_hit.get("match_id") or raw_hit.get("hit_id") or f"p{page_index}_e{element_index}_c{char_start}")
        matched_text = str(raw_hit.get("matched_text") or keyword or "").strip()
        context = raw_hit.get("context")
        if isinstance(context, list):
            context_text = " ".join(str(item).strip() for item in context if str(item).strip())
        elif isinstance(context, dict):
            context_text = " ".join(str(item).strip() for item in context.values() if str(item).strip())
        else:
            context_text = str(context or "").strip()
        points = ""
        if payload is not None and point_locator is not None:
            try:
                points = str(
                    point_locator.points_for_bbox(
                        payload=payload,
                        page_index=page_index,
                        bbox=bbox,
                        raw_hit=raw_hit,
                    )
                    or ""
                ).strip()
            except Exception as exc:
                logger.info("Circuit viewer points normalization skipped: %s", exc)
        results.append(
            {
                "hit_id": hit_id,
                "page_index": page_index,
                "page_number": page_index + 1,
                "bbox_px": bbox,
                "points": points,
                "matched_text": matched_text,
                "context": context_text[:240],
                "reading_order": reading_order,
                "element_index": element_index,
                "char_start": char_start,
            }
        )

    results.sort(
        key=lambda item: (
            int(item["page_index"]),
            int(item["reading_order"]),
            int(item["element_index"]),
            int(item["char_start"]),
            str(item["hit_id"]),
        )
    )
    return results


def _page_index(raw_hit: dict[str, Any]) -> int | None:
    try:
        page_index = int(raw_hit.get("page_index"))
    except (TypeError, ValueError):
        return None
    if page_index < 0:
        return None
    return page_index


def _bbox(raw_hit: dict[str, Any]) -> list[float] | None:
    value = raw_hit.get("bounding_box") or raw_hit.get("bbox")
    if isinstance(value, dict):
        keys = ("x_min", "y_min", "x_max", "y_max")
        try:
            box = [float(value[key]) for key in keys]
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            if isinstance(value[0], (list, tuple)):
                xs = [float(point[0]) for point in value]
                ys = [float(point[1]) for point in value]
                box = [min(xs), min(ys), max(xs), max(ys)]
            else:
                box = [float(part) for part in value]
        except (TypeError, ValueError, IndexError):
            return None
    else:
        return None

    left, right = sorted((box[0], box[2]))
    top, bottom = sorted((box[1], box[3]))
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
