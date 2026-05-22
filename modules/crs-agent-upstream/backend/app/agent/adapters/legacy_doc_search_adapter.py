"""Adapter layer for migrated legacy doc_search capabilities."""

import asyncio
from typing import Any

from app.agent.domain.doc_search.models import DocSearchRequest
from app.agent.domain.doc_search.service import DocSearchService
from app.agent.domain.doc_search.llm_smart import PydanticAIDocSearchLLMClarifyService
from app.agent.models.tool_result import (
    ClarifyCandidate,
    ClarifyCandidateOption,
    SelectionPayload,
    ToolResultEnvelope,
    ToolResultStatus,
)
from app.agent.runtime.deps import AgentRuntimeDeps
from app.core.config import settings
from app.legacy.services.ggzj.search_client import TokenExpiredError
from app.legacy.services.clarify_service import ClarifyService
from app.legacy.services.existence_validator import ExistenceValidator
from app.legacy.services.hard_constraint_validator import hard_constraint_validator as default_hard_constraint_validator
from app.legacy.services.search_engine import SearchEngine


class LegacyDocSearchAdapter:
    """Bridge between Agent Loop tools and migrated legacy doc_search services."""

    def __init__(self, deps: AgentRuntimeDeps):
        self._deps = deps
        search_top_k_lex = int(
            deps.config_service.get("search_top_k_lex", settings.search_top_k_lex)
            if deps.config_service is not None
            else settings.search_top_k_lex
        )
        self._service = DocSearchService(
            db_session_factory=deps.db_session_factory,
            search_engine_factory=deps.search_engine_factory or SearchEngine,
            clarify_service=deps.clarify_service or ClarifyService(),
            dimension_service=getattr(deps, "dimension_service", None),
            existence_validator=deps.existence_validator or ExistenceValidator(),
            hard_constraint_validator=deps.hard_constraint_validator or default_hard_constraint_validator,
            search_top_k_lex=search_top_k_lex,
            config_service=deps.config_service,
            llm_clarify_service=deps.llm_clarify_service
            or PydanticAIDocSearchLLMClarifyService(config_service=deps.config_service),
        )

    async def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        top_k: int = 20,
        selection_payload: dict[str, Any] | None = None,
    ) -> dict:
        if self._deps.enforce_external_doc_search and not self._deps.app_token:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={
                    "message": "未登录，请重新进入",
                    "reason": "missing_app_token",
                    "error_code": "TOKEN_REQUIRED",
                },
            ).model_dump(mode="json")

        if self._deps.db_session_factory is None and not self._deps.app_token:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "db_session_factory is not configured."},
            ).model_dump(mode="json")

        doc_request = DocSearchRequest(
            query=query,
            original_query=query,
            filters=filters or {},
            top_k=top_k,
            selection_payload=selection_payload or {},
        )
        try:
            if self._deps.app_token:
                execution = await self._service.execute_external(
                    doc_request,
                    app_token=self._deps.app_token,
                    cache_store=self._deps.doc_search_cache_store,
                    search_client=self._deps.ggzj_search_client,
                    result_adapter=self._deps.ggzj_result_adapter,
                )
            else:
                execution = await asyncio.to_thread(
                    self._service.execute,
                    doc_request,
                )
            return ToolResultEnvelope(
                status=ToolResultStatus.OK,
                data=execution.to_tool_data(),
            ).model_dump(mode="json")
        except TokenExpiredError as exc:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={
                    "message": "登录已失效，请重新登录",
                    "reason": str(exc),
                    "error_code": "TOKEN_EXPIRED",
                },
            ).model_dump(mode="json")
        except Exception as exc:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "Legacy doc search failed.", "reason": str(exc)},
            ).model_dump(mode="json")

    async def search_from_snapshot(
        self,
        *,
        query: str,
        snapshot: dict[str, Any],
        filters: dict[str, Any] | None = None,
        top_k: int = 20,
        selection_payload: dict[str, Any] | None = None,
    ) -> dict:
        doc_request = DocSearchRequest(
            query=query,
            original_query=snapshot.get("original_user_query") or snapshot.get("original_query") or query,
            filters=filters or {},
            top_k=top_k,
            selection_payload=selection_payload or {},
        )
        try:
            execution = self._service.execute_from_snapshot(doc_request, snapshot=snapshot)
            return ToolResultEnvelope(
                status=ToolResultStatus.OK,
                data=execution.to_tool_data(),
            ).model_dump(mode="json")
        except Exception as exc:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "Legacy doc search snapshot resume failed.", "reason": str(exc)},
            ).model_dump(mode="json")

    async def search_raw(
        self,
        query: str,
        top_k: int = 20,
    ) -> dict:
        if self._deps.enforce_external_doc_search and not self._deps.app_token:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={
                    "message": "未登录，请重新进入",
                    "reason": "missing_app_token",
                    "error_code": "TOKEN_REQUIRED",
                },
            ).model_dump(mode="json")

        if self._deps.db_session_factory is None and not self._deps.app_token:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "db_session_factory is not configured."},
            ).model_dump(mode="json")

        doc_request = DocSearchRequest(
            query=query,
            original_query=query,
            filters={},
            top_k=top_k,
            selection_payload={},
        )
        try:
            if self._deps.app_token:
                raw = await self._service.execute_external_raw(
                    doc_request,
                    app_token=self._deps.app_token,
                    cache_store=self._deps.doc_search_cache_store,
                    search_client=self._deps.ggzj_search_client,
                    result_adapter=self._deps.ggzj_result_adapter,
                )
            else:
                raw = await asyncio.to_thread(
                    self._service.execute_raw,
                    doc_request,
                )
            return ToolResultEnvelope(
                status=ToolResultStatus.OK,
                data=raw,
            ).model_dump(mode="json")
        except TokenExpiredError as exc:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={
                    "message": "登录已失效，请重新登录",
                    "reason": str(exc),
                    "error_code": "TOKEN_EXPIRED",
                },
            ).model_dump(mode="json")
        except Exception as exc:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data={"message": "Legacy doc search raw search failed.", "reason": str(exc)},
            ).model_dump(mode="json")

    async def analyze_ambiguity(
        self,
        results: list[dict[str, Any]],
        preprocessing: dict[str, Any] | None = None,
        existing_filters: dict[str, Any] | None = None,
        query: str | None = None,
        validity: dict[str, Any] | None = None,
        clarify_round: int | None = None,
        user_has_structured_selection: bool | None = None,
    ) -> dict:
        analysis = await self._service.analyze_ambiguity(
            results=results,
            preprocessing=preprocessing,
            existing_filters=existing_filters,
            query=query,
            validity=validity,
            clarify_round=clarify_round,
            user_has_structured_selection=user_has_structured_selection,
        )
        if not analysis.need_clarify:
            return ToolResultEnvelope(
                status=ToolResultStatus.OK,
                data={"need_clarify": False},
            ).model_dump(mode="json")

        return ToolResultEnvelope(
            status=ToolResultStatus.NEED_CLARIFY,
            data={
                "need_clarify": True,
                "facet": analysis.facet,
                "reason": analysis.reason,
                "context": analysis.context.model_dump(mode="json") if analysis.context is not None else {},
            },
            clarify=ClarifyCandidate(
                source=analysis.source,
                question=analysis.question or "请补充筛选条件",
                results_count=analysis.results_count,
                context=analysis.context.model_dump(mode="json") if analysis.context is not None else {},
                options=[
                    ClarifyCandidateOption(
                        key=option.key,
                        label=option.label,
                        description=option.description,
                        selection_payload=SelectionPayload.model_validate(
                            option.selection_payload.model_dump(mode="json")
                        ),
                    )
                    for option in analysis.options
                ],
            ),
        ).model_dump(mode="json")
