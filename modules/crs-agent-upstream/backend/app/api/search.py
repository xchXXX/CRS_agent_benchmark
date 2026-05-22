"""Compatibility search endpoints for legacy frontend fallbacks."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.adapters.legacy_doc_search_adapter import LegacyDocSearchAdapter
from app.api.request_context import build_request_runtime_deps


router = APIRouter(tags=["search"])


class SearchRequestCompat(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    clarify_choice: str | None = None
    limit: int = 20


def _serialize_search_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": str(item.get("file_id") or ""),
        "file_id": str(item.get("file_id") or ""),
        "title": item.get("filename") or "",
        "path": item.get("hierarchy_full") or item.get("filename") or "",
        "ref_file_id": item.get("ref_file_id"),
        "parent_id": item.get("parent_id"),
        "pic_folder_url": item.get("pic_folder_url"),
        "ggzj_sn": item.get("ggzj_sn"),
        "ggzj_data_type": item.get("ggzj_data_type"),
        "ggzj_file_no": item.get("ggzj_file_no"),
        "ggzj_file_type": item.get("ggzj_file_type"),
        "tags": {
            "brand": item.get("brand"),
            "series": item.get("series"),
            "model": item.get("model"),
            "platform_codes": item.get("platform_codes") or [],
            "subsystems": item.get("subsystems") or [],
            "ecus": item.get("ecus") or [],
            "suppliers": item.get("suppliers") or [],
            "emissions": item.get("emissions") or [],
            "drive_types": item.get("drive_types") or [],
            "batches": item.get("batches") or [],
            "doc_types": item.get("doc_types") or [],
        },
        "score": float(item.get("score") or 0),
        "explain": [],
    }


@router.post("/search")
@router.post("/chat/api/search")
async def search(request: SearchRequestCompat, raw_request: Request):
    runtime_deps = await build_request_runtime_deps(raw_request)
    adapter = LegacyDocSearchAdapter(runtime_deps)
    result = await adapter.search(
        request.query,
        filters=request.filters,
        top_k=request.limit,
    )
    if result["status"] == "failed":
        error_code = result.get("data", {}).get("error_code")
        if error_code in {"TOKEN_EXPIRED", "TOKEN_REQUIRED"}:
            raise HTTPException(status_code=401, detail=result["data"].get("message", "未登录，请重新进入"))
        raise HTTPException(status_code=500, detail=result.get("data", {}).get("message", "搜索失败"))

    data = result["data"]
    ambiguity = await adapter.analyze_ambiguity(
        results=data.get("results", []),
        preprocessing=data.get("preprocessing"),
        existing_filters=data.get("applied_filters"),
        query=data.get("original_query"),
        validity=data.get("validity"),
        user_has_structured_selection=bool(
            (data.get("requested_filters") or {})
            or ((data.get("applied_selection_payload") or {}).get("filters") or {})
            or ((data.get("applied_selection_payload") or {}).get("file_ids") or [])
        ),
    )

    clarify = {"need": False}
    if ambiguity.get("status") == "need_clarify":
        clarify_info = ambiguity.get("clarify") or {}
        clarify = {
            "need": True,
            "facet": ambiguity.get("data", {}).get("facet"),
            "question": clarify_info.get("question"),
            "options": [option.get("label") for option in clarify_info.get("options", [])],
        }

    return {
        "results": [_serialize_search_result(item) for item in data.get("results", [])],
        "clarify": clarify,
        "stats": {
            "took_ms": data.get("search_time_ms") or 0,
            "candidates": data.get("total") or 0,
            "debug_info": {"search_method": data.get("search_method")},
        },
        "validity": data.get("validity") or {"has_valid_results": True},
    }
