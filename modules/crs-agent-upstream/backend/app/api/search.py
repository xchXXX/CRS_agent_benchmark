"""Compatibility search endpoints for legacy frontend fallbacks."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.adapters.legacy_doc_search_adapter import LegacyDocSearchAdapter
from app.agent.runtime.service import AgentLoopService, DocSearchExecutedQuery
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


async def _execute_search_with_rule_variants(
    *,
    adapter: LegacyDocSearchAdapter,
    runtime_deps: Any,
    request: SearchRequestCompat,
) -> dict[str, Any]:
    executed_queries = AgentLoopService._build_doc_search_rule_query_variants(
        query=request.query,
        active_deps=runtime_deps,
    ) or (DocSearchExecutedQuery(query=request.query, confidence=1.0),)

    raw_envelopes = []
    for query_info in executed_queries:
        raw_envelopes.append(
            (
                query_info,
                await adapter.search_raw(
                    query=query_info.query,
                    top_k=request.limit,
                ),
            )
        )

    merged_envelope = AgentLoopService._merge_doc_search_envelopes(
        raw_envelopes,
        primary_query=request.query,
    )
    if merged_envelope.get("status") != "ok":
        return merged_envelope

    merged_snapshot = dict(merged_envelope.get("data") or {})
    preprocessing_candidates = merged_snapshot.pop("validation_preprocessing_candidates", None)
    planned_queries = list(merged_snapshot.get("planned_queries") or [])
    candidate_preprocessings = [
        item
        for item in (preprocessing_candidates or [])
        if isinstance(item, dict)
    ]
    if not candidate_preprocessings and isinstance(merged_snapshot.get("preprocessing"), dict):
        candidate_preprocessings = [merged_snapshot["preprocessing"]]

    preprocessing_attempts = list(candidate_preprocessings)
    if AgentLoopService._doc_search_snapshot_has_strong_intent_match(
        snapshot=merged_snapshot,
        primary_query=request.query,
    ):
        preprocessing_attempts.append(None)
    if not preprocessing_attempts:
        preprocessing_attempts = [None]

    final_envelope: dict[str, Any] | None = None
    for preprocessing in preprocessing_attempts:
        snapshot = dict(merged_snapshot)
        if preprocessing is not None:
            snapshot["preprocessing"] = preprocessing
        else:
            snapshot.pop("preprocessing", None)
        final_envelope = await adapter.search_from_snapshot(
            query=request.query,
            snapshot=snapshot,
            filters=request.filters,
            top_k=request.limit,
        )
        if not isinstance(final_envelope, dict) or final_envelope.get("status") != "ok":
            break
        validity = (final_envelope.get("data") or {}).get("validity") or {}
        if validity.get("has_valid_results") is not False:
            break

    result = final_envelope or merged_envelope
    if result.get("status") == "ok" and isinstance(result.get("data"), dict):
        data = result["data"]
        if len(planned_queries) > 1:
            data["planned_queries"] = planned_queries
        else:
            data.pop("planned_queries", None)
    return result


@router.post("/search")
@router.post("/chat/api/search")
async def search(request: SearchRequestCompat, raw_request: Request):
    runtime_deps = await build_request_runtime_deps(raw_request)
    adapter = LegacyDocSearchAdapter(runtime_deps)
    result = await _execute_search_with_rule_variants(
        adapter=adapter,
        runtime_deps=runtime_deps,
        request=request,
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
            "debug_info": {
                "search_method": data.get("search_method"),
                "planned_queries": data.get("planned_queries") or [],
            },
        },
        "validity": data.get("validity") or {"has_valid_results": True},
    }
