"""Clarify presentation builders for doc_search."""

from typing import Any

from app.agent.domain.doc_search.models import (
    DocSearchClarifyContext,
    DocSearchExistence,
    DocSearchExistenceHint,
    DocSearchSelectionPayload,
    DocSearchTopResult,
)


class DocSearchClarifyResultBuilder:
    """Build clarify display payloads without reintroducing legacy session state."""

    @staticmethod
    def build_context(
        *,
        question: str,
        original_query: str,
        existing_filters: dict[str, Any],
        results_count: int,
        results: list[dict[str, Any]] | None = None,
        preprocessing: dict[str, Any] | None = None,
        existence: DocSearchExistence | dict[str, Any] | None = None,
        clarify_round: int | None = None,
        include_results_prefix: bool = True,
    ) -> DocSearchClarifyContext:
        top_result = DocSearchClarifyResultBuilder._build_top_result(
            results=results or [],
            existing_filters=existing_filters,
            preprocessing=preprocessing,
        )
        existence_hint = DocSearchClarifyResultBuilder._build_existence_hint(existence)
        message = question
        if include_results_prefix:
            message = f"找到 {results_count} 个相关结果。{question}"

        return DocSearchClarifyContext(
            message=message,
            query=original_query,
            results_count=results_count,
            clarify_round=clarify_round or 1,
            top_result=top_result,
            existence_info=existence_hint,
        )

    @staticmethod
    def build_selection_payload(
        *,
        existing_filters: dict[str, Any] | None = None,
        extra_filters: dict[str, Any] | None = None,
        file_ids: list[str] | None = None,
    ) -> DocSearchSelectionPayload:
        merged_filters: dict[str, Any] = {}
        for source in (existing_filters or {}, extra_filters or {}):
            for key, value in source.items():
                if key.startswith("_") or value in (None, ""):
                    continue
                merged_filters[str(key)] = value
        return DocSearchSelectionPayload(
            filters=merged_filters,
            file_ids=[str(item) for item in (file_ids or []) if item not in (None, "")],
        )

    @staticmethod
    def _build_top_result(
        *,
        results: list[dict[str, Any]],
        existing_filters: dict[str, Any],
        preprocessing: dict[str, Any] | None,
    ) -> DocSearchTopResult | None:
        if not results:
            return None

        has_brand = bool(existing_filters.get("brand"))
        if not has_brand and preprocessing:
            has_brand = bool((preprocessing.get("entities") or {}).get("brand"))
        if not has_brand:
            return None

        top = results[0]
        if not top.get("pic_folder_url") or not top.get("file_id"):
            return None

        return DocSearchTopResult(
            file_id=str(top.get("file_id")),
            title=top.get("filename"),
            score=top.get("score"),
            pic_folder_url=top.get("pic_folder_url"),
            brand=top.get("brand"),
            series=top.get("series"),
            model=top.get("model"),
            selection_payload=DocSearchClarifyResultBuilder.build_selection_payload(
                existing_filters=existing_filters,
                file_ids=[str(top.get("file_id"))],
            ),
        )

    @staticmethod
    def _build_existence_hint(existence: DocSearchExistence | dict[str, Any] | None) -> DocSearchExistenceHint | None:
        if existence is None:
            return None

        existence_data = existence
        if isinstance(existence, dict):
            existence_data = DocSearchExistence.model_validate(existence)

        if existence_data.status == "exact_match":
            return None

        return DocSearchExistenceHint(
            status=existence_data.status,
            message=existence_data.message,
            suggestions=existence_data.suggestions,
        )
