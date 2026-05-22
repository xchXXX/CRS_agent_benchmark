"""User frontend runtime configuration."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.agent.runtime.deps import AgentRuntimeDeps
from app.agent.domain.circuit_body_search.preview_token import (
    CircuitBodyPreviewTokenCodec,
    DEFAULT_PREVIEW_TOKEN_TTL_SECONDS,
)
from app.core.config import settings


router = APIRouter(prefix="/frontend", tags=["frontend-runtime"])


class FrontendRuntimeConfig(BaseModel):
    eruda_enabled: bool
    webview_debug_enabled: bool
    webview_debug_url: str = ""
    webview_debug_viewer_token: str = ""


def _get_runtime_deps(request: Request) -> AgentRuntimeDeps:
    runtime_deps = getattr(request.app.state, "runtime_deps", None)
    if runtime_deps is not None:
        return runtime_deps

    runtime_deps = AgentRuntimeDeps.build_default()
    request.app.state.runtime_deps = runtime_deps
    return runtime_deps


@router.get("/runtime-config", response_model=FrontendRuntimeConfig)
async def get_frontend_runtime_config(request: Request) -> FrontendRuntimeConfig:
    runtime_deps = _get_runtime_deps(request)
    eruda_enabled = settings.frontend_eruda_enabled
    webview_debug_enabled = settings.frontend_webview_debug_enabled
    webview_debug_url = settings.frontend_webview_debug_url
    webview_debug_pdf_id = settings.frontend_webview_debug_pdf_id
    if runtime_deps.config_service is not None:
        eruda_enabled = bool(runtime_deps.config_service.get("frontend_eruda_enabled", eruda_enabled))
        webview_debug_enabled = bool(
            runtime_deps.config_service.get("frontend_webview_debug_enabled", webview_debug_enabled)
        )
        webview_debug_url = str(
            runtime_deps.config_service.get("frontend_webview_debug_url", webview_debug_url) or ""
        )
        webview_debug_pdf_id = str(
            runtime_deps.config_service.get("frontend_webview_debug_pdf_id", webview_debug_pdf_id) or ""
        )

    viewer_token = ""
    if webview_debug_enabled and webview_debug_url and webview_debug_pdf_id:
        codec = getattr(runtime_deps, "circuit_body_preview_token_codec", None) or CircuitBodyPreviewTokenCodec()
        viewer_token = codec.encode(
            {
                "pdf_id": webview_debug_pdf_id,
                "filename": "WebView 图内搜索调试",
                "keyword": "",
                "hit_id": "",
                "latest_result_path": "",
                "source_pdf_url": webview_debug_url,
                "page_index": 1,
                "highlight_boxes_px": [],
            },
            ttl_seconds=DEFAULT_PREVIEW_TOKEN_TTL_SECONDS,
        )

    return FrontendRuntimeConfig(
        eruda_enabled=eruda_enabled,
        webview_debug_enabled=webview_debug_enabled,
        webview_debug_url=webview_debug_url,
        webview_debug_viewer_token=viewer_token,
    )
