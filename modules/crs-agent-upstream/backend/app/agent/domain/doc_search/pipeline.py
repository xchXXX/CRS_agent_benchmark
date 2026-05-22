"""Post-search pipeline for doc_search domain."""

import unicodedata
from collections.abc import Callable
from typing import Any

from app.agent.domain.doc_search.matching import DocSearchResultMatcher
from app.agent.domain.doc_search.models import (
    DocSearchExecutionResult,
    DocSearchExistence,
    DocSearchHardConstraint,
    DocSearchRequest,
    DocSearchSelectionPayload,
    DocSearchValidity,
)
from app.agent.domain.doc_search.policies import DocSearchEntityFilterPolicy
from app.agent.domain.doc_search.builders import DocSearchSummaryBuilder


class DocSearchPipeline:
    """Fixed processing chain for search result normalization and validation."""

    def __init__(
        self,
        *,
        clarify_service: Any,
        dimension_service: Any,
        existence_validator: Any,
        hard_constraint_validator: Any,
        summary_builder: DocSearchSummaryBuilder,
        hard_constraint_enabled_provider: Callable[[], bool] | None = None,
    ):
        self._clarify_service = clarify_service
        self._dimension_service = dimension_service
        self._existence_validator = existence_validator
        self._hard_constraint_validator = hard_constraint_validator
        self._summary_builder = summary_builder
        self._hard_constraint_enabled_provider = hard_constraint_enabled_provider or (lambda: True)
        self._matcher = DocSearchResultMatcher(
            clarify_service=clarify_service,
            dimension_service=dimension_service,
        )
        self._entity_filter_policy = DocSearchEntityFilterPolicy(
            matcher=self._matcher,
            dimension_service=dimension_service,
        )

    def finalize_search(
        self,
        *,
        request: DocSearchRequest,
        raw: dict[str, Any],
    ) -> DocSearchExecutionResult:
        results = list(raw.get("results", []))
        preprocessing = raw.get("preprocessing")
        exact_match_query = request.original_query or request.query
        effective_filters = self.merge_filters(request.selection_payload, request.filters)
        selected_file_ids = request.selection_payload.file_ids
        has_user_structured_selection = bool(request.filters or request.selection_payload.filters or selected_file_ids)
        has_exact_top_result = (
            not has_user_structured_selection
            and self._is_top_result_exact_query_match(results, exact_match_query)
        )

        if has_exact_top_result:
            results = [results[0]]

        if selected_file_ids:
            allowed_ids = set(selected_file_ids)
            results = [item for item in results if str(item.get("file_id")) in allowed_ids]

        regular_filters, _ = self._entity_filter_policy.split_filters(effective_filters)
        effective_filters = dict(regular_filters)

        if regular_filters:
            results = self.apply_filters_strict(results, regular_filters)

        if not has_exact_top_result and not has_user_structured_selection and preprocessing:
            auto_outcome = self._entity_filter_policy.apply_initial(
                results=results,
                preprocessing=preprocessing,
            )
            results = auto_outcome.results
            effective_filters = auto_outcome.applied_filters

        validity = self.build_validity(
            results=results,
            preprocessing=preprocessing,
            has_structured_selection=has_user_structured_selection,
        )
        if not validity.has_valid_results:
            results = []

        final_results = list(results)
        summary = None
        summary_query = None
        result_summary = None
        if validity.has_valid_results:
            summary_query, summary = self._summary_builder.build_summary_text(
                original_query=request.query,
                filters=effective_filters,
                total_hits=len(results),
                returned_count=len(final_results),
            )
            result_summary = self._summary_builder.build_result_summary(
                original_query=request.query,
                filters=effective_filters,
                results=final_results,
            )
        return DocSearchExecutionResult(
            query=raw.get("query", request.query),
            original_query=request.original_query or request.query,
            results=final_results,
            total=len(results),
            preprocessing=preprocessing,
            search_method=raw.get("search_method"),
            search_time_ms=raw.get("search_time_ms"),
            requested_filters=dict(request.filters),
            applied_filters=effective_filters,
            applied_selection_payload=request.selection_payload,
            validity=validity,
            summary=summary,
            summary_query=summary_query,
            result_summary=result_summary,
        )

    @classmethod
    def _is_top_result_exact_query_match(cls, results: list[dict[str, Any]], query: str) -> bool:
        if not results or not query:
            return False

        query_norm = cls._normalize_exact_title(query)
        if not query_norm:
            return False

        top_result = results[0]
        for field_name in ("filename", "title"):
            value = top_result.get(field_name)
            if value and cls._normalize_exact_title(str(value)) == query_norm:
                return True
        return False

    @staticmethod
    def _normalize_exact_title(value: str) -> str:
        return unicodedata.normalize("NFKC", str(value)).strip()

    @staticmethod
    def merge_filters(
        selection_payload: DocSearchSelectionPayload,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        merged = {str(key): value for key, value in selection_payload.filters.items()}
        for key, value in filters.items():
            merged[str(key)] = value
        return merged

    def apply_filters_strict(
        self,
        results: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        filtered = list(results)

        for facet, choice in filters.items():
            if choice in (None, ""):
                continue

            next_results: list[dict[str, Any]] = []
            for item in filtered:
                if self._matcher.matches_facet(item, facet, str(choice)):
                    next_results.append(item)
            filtered = next_results

        return filtered

    def build_validity(
        self,
        *,
        results: list[dict[str, Any]],
        preprocessing: dict[str, Any] | None,
        has_structured_selection: bool,
    ) -> DocSearchValidity:
        if not results:
            reason = "selection_payload_no_match" if has_structured_selection else "no_results"
            message = "根据已选择的条件未找到资料。" if has_structured_selection else "未找到相关资料。"
            return DocSearchValidity(
                has_valid_results=False,
                reason=reason,
                message=message,
            )

        if has_structured_selection or not preprocessing:
            return DocSearchValidity(has_valid_results=True)

        hc_data = None
        if self._hard_constraint_enabled_provider():
            hc = self._hard_constraint_validator.validate(results, preprocessing)
            hc_data = DocSearchHardConstraint(
                ok=hc.ok,
                missing_tokens=hc.missing_tokens,
                checked_tokens=hc.checked_tokens,
                message=hc.message,
            )
            if not hc.ok:
                return DocSearchValidity(
                    has_valid_results=False,
                    reason="hard_constraint_no_match",
                    message=hc.message or "抱歉，暂无相关资料在数据库中。",
                    hard_constraint=hc_data,
                )

        existence = self._existence_validator.validate(results, preprocessing)
        existence_data = DocSearchExistence(
            status=existence.status,
            query_entities=existence.query_entities,
            matched_entities=existence.matched_entities,
            unmatched_entities=existence.unmatched_entities,
            suggestions=existence.suggestions,
            message=existence.message,
            should_continue=existence.should_continue,
        )
        if existence.status == "no_match":
            return DocSearchValidity(
                has_valid_results=False,
                reason="existence_no_match",
                message=existence.message or "未找到相关资料。",
                hard_constraint=hc_data,
                existence=existence_data,
            )

        return DocSearchValidity(
            has_valid_results=True,
            reason=existence.status if existence.status != "exact_match" else None,
            message=existence.message,
            hard_constraint=hc_data,
            existence=existence_data,
        )
