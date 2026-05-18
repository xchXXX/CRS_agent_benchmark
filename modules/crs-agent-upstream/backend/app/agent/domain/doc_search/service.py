"""Facade service for doc_search domain."""

import hashlib
import time
from typing import Any

from app.agent.domain.doc_search.builders import (
    DocSearchClarifyResultBuilder,
    DocSearchSummaryBuilder,
)
from app.agent.domain.doc_search.llm_smart import PydanticAIDocSearchLLMClarifyService
from app.agent.domain.doc_search.matching import DocSearchResultMatcher
from app.agent.domain.doc_search.models import (
    DocSearchAmbiguityAnalysis,
    DocSearchAmbiguityOption,
    DocSearchRequest,
)
from app.agent.domain.doc_search.pipeline import DocSearchPipeline


class DocSearchService:
    """Stable domain entrypoint for doc_search execution and ambiguity analysis."""

    _OTHER_LIKE_OPTIONS = {"其他", "不确定"}

    def __init__(
        self,
        *,
        db_session_factory: Any,
        search_engine_factory: Any,
        clarify_service: Any,
        dimension_service: Any | None = None,
        existence_validator: Any,
        hard_constraint_validator: Any,
        search_top_k_lex: int,
        config_service: Any | None = None,
        llm_clarify_service: Any | None = None,
    ):
        self._db_session_factory = db_session_factory
        self._search_engine_factory = search_engine_factory
        self._clarify_service = clarify_service
        self._dimension_service = dimension_service
        self._search_top_k_lex = search_top_k_lex
        self._config_service = config_service
        self._clarify_result_builder = DocSearchClarifyResultBuilder()
        self._summary_builder = DocSearchSummaryBuilder(dimension_service=dimension_service)
        self._matcher = DocSearchResultMatcher(
            clarify_service=clarify_service,
            dimension_service=dimension_service,
        )
        self._llm_clarify_service = llm_clarify_service or PydanticAIDocSearchLLMClarifyService(
            config_service=config_service
        )
        self._pipeline = DocSearchPipeline(
            clarify_service=clarify_service,
            dimension_service=dimension_service,
            existence_validator=existence_validator,
            hard_constraint_validator=hard_constraint_validator,
            summary_builder=self._summary_builder,
            hard_constraint_enabled_provider=self._is_hard_constraint_enabled,
        )

    def execute(self, request: DocSearchRequest):
        raw = self.execute_raw(request)
        return self._pipeline.finalize_search(request=request, raw=raw)

    def execute_raw(self, request: DocSearchRequest) -> dict[str, Any]:
        if self._db_session_factory is None:
            raise RuntimeError("db_session_factory is not configured.")

        db = self._db_session_factory()
        try:
            engine_top_k = self._build_engine_top_k(request)
            engine = self._search_engine_factory(db)
            return engine.search(
                request.query,
                top_k=engine_top_k,
                lexical_top_k=self._search_top_k_lex,
                use_vector=False,
            )
        finally:
            db.close()

    def execute_from_snapshot(
        self,
        request: DocSearchRequest,
        *,
        snapshot: dict[str, Any],
    ):
        raw = {
            "query": snapshot.get("query", request.query),
            "results": list(snapshot.get("results", [])),
            "preprocessing": snapshot.get("preprocessing"),
            "search_method": snapshot.get("search_method"),
            "search_time_ms": snapshot.get("search_time_ms"),
        }
        return self._pipeline.finalize_search(request=request, raw=raw)

    async def execute_external(
        self,
        request: DocSearchRequest,
        *,
        app_token: str,
        cache_store: Any | None = None,
        search_client: Any | None = None,
        result_adapter: Any | None = None,
    ):
        raw = await self.execute_external_raw(
            request,
            app_token=app_token,
            cache_store=cache_store,
            search_client=search_client,
            result_adapter=result_adapter,
        )
        return self._pipeline.finalize_search(request=request, raw=raw)

    async def execute_external_raw(
        self,
        request: DocSearchRequest,
        *,
        app_token: str,
        cache_store: Any | None = None,
        search_client: Any | None = None,
        result_adapter: Any | None = None,
    ) -> dict[str, Any]:
        from app.legacy.services.ggzj import GgzjResultAdapter, GgzjSearchClient

        cache_key = self._build_external_cache_key(app_token, request.query)
        raw = cache_store.load(cache_key) if cache_store is not None else None
        if raw is None:
            client = search_client or GgzjSearchClient()
            adapter = result_adapter or GgzjResultAdapter()
            started = time.perf_counter()
            raw_items = await client.search(query=request.query, app_token=app_token)
            results, preprocessing = adapter.adapt_list(raw_items, request.query)
            raw = {
                "query": request.query,
                "results": results,
                "preprocessing": preprocessing,
                "search_method": "ggzj_external",
                "search_time_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            if cache_store is not None:
                cache_store.save(cache_key, raw)
        else:
            raw = dict(raw)
            raw["search_method"] = raw.get("search_method") or "ggzj_external_cached"

        return raw

    async def analyze_ambiguity(
        self,
        *,
        results: list[dict[str, Any]],
        preprocessing: dict[str, Any] | None = None,
        existing_filters: dict[str, Any] | None = None,
        query: str | None = None,
        validity: dict[str, Any] | None = None,
        clarify_round: int | None = None,
        user_has_structured_selection: bool | None = None,
    ) -> DocSearchAmbiguityAnalysis:
        existing_filters = existing_filters or {}
        original_query = query or (preprocessing or {}).get("original_query") or ""
        existence_info = (validity or {}).get("existence")
        current_clarify_round = clarify_round or 1

        should_run_preflight = preprocessing is not None and not bool(user_has_structured_selection)
        if should_run_preflight:
            preflight = self._analyze_preflight_ambiguity(
                preprocessing=preprocessing,
                results_count=len(results),
                original_query=original_query,
                existing_filters=existing_filters,
                results=results,
                existence_info=existence_info,
                clarify_round=current_clarify_round,
            )
            if preflight is not None:
                return preflight

        decision = self._clarify_service.analyze(
            results=results,
            preprocessing=preprocessing,
            existing_filters=existing_filters,
            clarify_round=max(current_clarify_round - 1, 0),
        )
        if decision.need:
            question = decision.question or "请补充筛选条件"
            return DocSearchAmbiguityAnalysis(
                need_clarify=True,
                facet=decision.facet,
                reason=decision.reason,
                question=question,
                source="rule",
                results_count=len(results),
                options=[
                    DocSearchAmbiguityOption(
                        key=option,
                        label=option,
                        selection_payload=self._build_rule_option_selection_payload(
                            option=option,
                            all_options=decision.options,
                            facet=decision.facet or "unknown",
                            existing_filters=existing_filters,
                            results=results,
                        ),
                    )
                    for option in decision.options
                ],
                context=self._clarify_result_builder.build_context(
                    question=question,
                    original_query=original_query,
                    existing_filters=existing_filters,
                    results_count=len(results),
                    results=results,
                    preprocessing=preprocessing,
                    existence=existence_info,
                    clarify_round=current_clarify_round,
                ),
            )

        llm_min = int(self._get_config("llm_clarify_min_results", 5))
        if len(results) > llm_min and self._llm_clarify_service is not None:
            llm_result = await self._llm_clarify_service.analyze(
                results=results,
                query=original_query,
                existing_filters=existing_filters,
                user_intent_entities=(preprocessing or {}).get("entities"),
            )
            if llm_result is not None and llm_result.options:
                llm_display_results = self._select_llm_display_results(results, llm_result.options)
                question = llm_result.question or "请选择："
                return DocSearchAmbiguityAnalysis(
                    need_clarify=True,
                    facet="llm_smart",
                    reason=llm_result.reason,
                    question=question,
                    source="llm",
                    results_count=len(llm_display_results),
                    options=[
                        DocSearchAmbiguityOption(
                            key=option.label,
                            label=option.label,
                            description=option.description,
                            selection_payload=self._clarify_result_builder.build_selection_payload(
                                existing_filters=existing_filters,
                                file_ids=option.file_ids,
                            ),
                        )
                        for option in llm_result.options
                    ],
                    context=self._clarify_result_builder.build_context(
                        question=question,
                        original_query=original_query,
                        existing_filters=existing_filters,
                        results_count=len(llm_display_results),
                        results=llm_display_results,
                        preprocessing=preprocessing,
                        existence=existence_info,
                        clarify_round=current_clarify_round,
                    ),
                )

        return DocSearchAmbiguityAnalysis()

    @staticmethod
    def _select_llm_display_results(
        results: list[dict[str, Any]],
        options: list[Any],
    ) -> list[dict[str, Any]]:
        allowed_file_ids: set[str] = set()
        for option in options:
            for file_id in getattr(option, "file_ids", []) or []:
                if file_id not in (None, ""):
                    allowed_file_ids.add(str(file_id))

        if not allowed_file_ids:
            return list(results)

        filtered = [
            item for item in results
            if str(item.get("file_id")) in allowed_file_ids
        ]
        return filtered or list(results)

    def _build_rule_option_selection_payload(
        self,
        *,
        option: str,
        all_options: list[str],
        facet: str,
        existing_filters: dict[str, Any],
        results: list[dict[str, Any]],
    ):
        if option not in self._OTHER_LIKE_OPTIONS:
            return self._clarify_result_builder.build_selection_payload(
                existing_filters=existing_filters,
                extra_filters={facet: option},
            )

        other_file_ids = self._collect_other_option_file_ids(
            facet=facet,
            all_options=all_options,
            results=results,
        )
        return self._clarify_result_builder.build_selection_payload(
            existing_filters=existing_filters,
            file_ids=other_file_ids,
        )

    def _collect_other_option_file_ids(
        self,
        *,
        facet: str,
        all_options: list[str],
        results: list[dict[str, Any]],
    ) -> list[str]:
        explicit_options = [
            item for item in all_options
            if item not in self._OTHER_LIKE_OPTIONS
        ]
        result_file_ids = [
            str(item.get("file_id"))
            for item in results
            if item.get("file_id") not in (None, "")
        ]
        if not explicit_options:
            return result_file_ids

        covered_file_ids = self._collect_explicit_option_representative_file_ids(
            facet=facet,
            explicit_options=explicit_options,
            results=results,
        )
        other_file_ids = [
            file_id
            for file_id in result_file_ids
            if file_id not in covered_file_ids
        ]

        if other_file_ids:
            return other_file_ids

        return result_file_ids

    def _collect_explicit_option_representative_file_ids(
        self,
        *,
        facet: str,
        explicit_options: list[str],
        results: list[dict[str, Any]],
    ) -> set[str]:
        covered_file_ids: set[str] = set()

        for option in explicit_options:
            for item in results:
                file_id = item.get("file_id")
                if file_id in (None, ""):
                    continue

                file_id_text = str(file_id)
                if file_id_text in covered_file_ids:
                    continue

                if self._matcher.matches_facet(item, facet, str(option)):
                    covered_file_ids.add(file_id_text)
                    break

        return covered_file_ids

    def _analyze_preflight_ambiguity(
        self,
        *,
        preprocessing: dict[str, Any],
        results_count: int,
        original_query: str,
        existing_filters: dict[str, Any],
        results: list[dict[str, Any]],
        existence_info: dict[str, Any] | None,
        clarify_round: int,
    ) -> DocSearchAmbiguityAnalysis | None:
        entities = preprocessing.get("entities") or {}

        if self._dimension_service is not None and getattr(self._dimension_service, "is_loaded", False):
            conflicts = self._dimension_service.detect_conflicts(entities)
            if conflicts:
                conflict = conflicts[0]
                return DocSearchAmbiguityAnalysis(
                    need_clarify=True,
                    facet="_conflict",
                    reason="input_conflict",
                    question=conflict.message,
                    source="rule",
                    results_count=results_count,
                    options=[
                        DocSearchAmbiguityOption(
                            key=str(option.get("key", option.get("label", ""))),
                            label=str(option.get("label", "")),
                            description=option.get("description"),
                            selection_payload=self._clarify_result_builder.build_selection_payload(
                                existing_filters=existing_filters,
                                extra_filters=option.get("filters", {}),
                            ),
                        )
                        for option in conflict.options
                    ],
                    context=self._clarify_result_builder.build_context(
                        question=conflict.message,
                        original_query=original_query,
                        existing_filters=existing_filters,
                        results_count=results_count,
                        results=results,
                        preprocessing=preprocessing,
                        existence=existence_info,
                        clarify_round=clarify_round,
                        include_results_prefix=False,
                    ),
                )

        brands = entities.get("brand", [])
        unique_brands = list(dict.fromkeys(brands))
        if len(unique_brands) > 1:
            question = "检测到多个品牌，请先选择您要查找的品牌："
            return DocSearchAmbiguityAnalysis(
                need_clarify=True,
                facet="brand",
                reason="multi_brand_ambiguity",
                question=question,
                source="rule",
                results_count=results_count,
                options=[
                    DocSearchAmbiguityOption(
                        key=brand,
                        label=brand,
                        selection_payload=self._clarify_result_builder.build_selection_payload(
                            existing_filters=existing_filters,
                            extra_filters={"brand": brand},
                        ),
                    )
                    for brand in unique_brands
                ],
                context=self._clarify_result_builder.build_context(
                    question=question,
                    original_query=original_query,
                    existing_filters=existing_filters,
                    results_count=results_count,
                    results=results,
                    preprocessing=preprocessing,
                    existence=existence_info,
                    clarify_round=clarify_round,
                    include_results_prefix=False,
                ),
            )

        return None

    def _get_config(self, key: str, default: Any) -> Any:
        if self._config_service is None:
            return default
        return self._config_service.get(key, default)

    def _is_hard_constraint_enabled(self) -> bool:
        return bool(self._get_config("hard_constraint_enabled", True))

    def _build_engine_top_k(self, request: DocSearchRequest) -> int:
        engine_top_k = max(request.top_k, self._search_top_k_lex)
        if request.selection_payload.file_ids:
            engine_top_k = max(engine_top_k, len(request.selection_payload.file_ids), 30)
        return engine_top_k

    @staticmethod
    def _build_external_cache_key(app_token: str, query: str) -> str:
        digest = hashlib.sha256(f"{app_token}::{query}".encode("utf-8")).hexdigest()
        return f"ggzj_query_{digest}"
