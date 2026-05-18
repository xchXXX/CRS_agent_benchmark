"""Entity filtering policy for doc_search."""

from dataclasses import dataclass, field
from typing import Any

from app.agent.domain.doc_search.matching import DocSearchResultMatcher


@dataclass
class DocSearchEntityFilterOutcome:
    results: list[dict[str, Any]]
    applied_filters: dict[str, Any] = field(default_factory=dict)


class DocSearchEntityFilterPolicy:
    """Policy for auto entity filters and special extra filters."""

    def __init__(self, *, matcher: DocSearchResultMatcher, dimension_service: Any | None = None):
        self._matcher = matcher
        if dimension_service is None:
            from app.legacy.services.dimension_service import dimension_service as global_dimension_service

            dimension_service = global_dimension_service
        self._dimension_service = dimension_service

    def apply_initial(
        self,
        *,
        results: list[dict[str, Any]],
        preprocessing: dict[str, Any] | None,
        existing_filters: dict[str, Any] | None = None,
    ) -> DocSearchEntityFilterOutcome:
        if not results or not preprocessing:
            return DocSearchEntityFilterOutcome(results=list(results), applied_filters=dict(existing_filters or {}))

        filtered = list(results)
        applied_filters = dict(existing_filters or {})
        entities = preprocessing.get("entities") or {}

        for facet in ["brand", "series", "model", "doc_type", "emissions"]:
            if facet in applied_filters:
                continue

            values = entities.get(facet, [])
            unique_values = list(dict.fromkeys(values))
            choice = self._resolve_auto_choice(
                candidates=filtered,
                facet=facet,
                values=unique_values,
            )
            if choice is None:
                continue
            if facet in {"brand", "series", "model"} and self._is_dimension_service_loaded():
                root_choice = self._dimension_service.get_root_value_in_facet(facet, choice)
                if root_choice != choice:
                    choice = root_choice

            if not self._has_choice_match(filtered, facet, choice):
                continue

            if facet in {"series", "brand"}:
                platform_values = entities.get("platform", [])
                if platform_values:
                    tentative = self._filter_by_choice(filtered, facet, choice)
                    if not any(self._result_has_platform(item, platform_values) for item in tentative):
                        continue

            filtered = self._filter_by_choice(filtered, facet, choice)
            applied_filters[facet] = choice

            if self._is_dimension_service_loaded():
                parent = self._dimension_service.get_parent(facet, choice)
                if parent:
                    parent_facet, parent_value = parent
                    if parent_facet not in applied_filters and self._has_choice_match(filtered, parent_facet, parent_value):
                        filtered = self._filter_by_choice(filtered, parent_facet, parent_value)
                        applied_filters[parent_facet] = parent_value

        return DocSearchEntityFilterOutcome(results=filtered, applied_filters=applied_filters)

    def split_filters(self, filters: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]]]:
        regular_filters: dict[str, Any] = {}
        dropped_extra_entities: dict[str, list[str]] = {}
        for facet, choice in filters.items():
            if facet == "eng_code":
                dropped_extra_entities[facet] = [str(choice)]
                continue
            regular_filters[facet] = choice
        return regular_filters, dropped_extra_entities

    def _has_choice_match(self, candidates: list[dict[str, Any]], facet_name: str, selected: str) -> bool:
        for item in candidates:
            if self._matcher.matches_facet(item, facet_name, selected):
                return True
        return False

    def _filter_by_choice(
        self,
        results: list[dict[str, Any]],
        facet: str,
        choice: str,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for item in results:
            if self._matcher.matches_facet(item, facet, choice):
                filtered.append(item)
        return filtered

    @staticmethod
    def _result_has_platform(result: dict[str, Any], platform_values: list[str]) -> bool:
        for platform_value in platform_values:
            pv_lower = str(platform_value).lower()
            platform_codes = result.get("platform_codes") or []
            if isinstance(platform_codes, list) and any(pv_lower == str(code).lower() for code in platform_codes):
                return True
            eng_codes = result.get("eng_codes") or []
            if isinstance(eng_codes, list) and any(pv_lower == str(code).lower() for code in eng_codes):
                return True
            filename = result.get("filename") or ""
            if pv_lower in filename.lower():
                return True
        return False

    def _is_dimension_service_loaded(self) -> bool:
        return bool(self._dimension_service and getattr(self._dimension_service, "is_loaded", False))

    def _resolve_auto_choice(
        self,
        *,
        candidates: list[dict[str, Any]],
        facet: str,
        values: list[str],
    ) -> str | None:
        if len(values) == 1:
            return str(values[0])
        if facet != "doc_type" or len(values) <= 1:
            return None

        narrowed = self._narrow_doc_type_values(values)
        if len(narrowed) == 1:
            return narrowed[0]

        return None

    def _narrow_doc_type_values(self, values: list[str]) -> list[str]:
        normalized_values = [str(value) for value in values if str(value).strip()]
        if len(normalized_values) <= 1:
            return normalized_values

        removable: set[str] = set()
        if self._is_dimension_service_loaded():
            for value in normalized_values:
                parent = self._dimension_service.get_parent("doc_type", value)
                while parent:
                    parent_facet, parent_value = parent
                    if parent_facet != "doc_type":
                        break
                    if parent_value in normalized_values:
                        removable.add(parent_value)
                    parent = self._dimension_service.get_parent("doc_type", parent_value)

        if removable:
            pruned = [value for value in normalized_values if value not in removable]
            if pruned:
                normalized_values = pruned

        fallback_removable: set[str] = set()
        normalized_map = {
            value: self._matcher.normalize_for_compare(value)
            for value in normalized_values
        }
        for value in normalized_values:
            norm = normalized_map[value]
            if not norm:
                continue
            for other in normalized_values:
                if value == other:
                    continue
                other_norm = normalized_map[other]
                if not other_norm or norm == other_norm:
                    continue
                if norm in other_norm and len(norm) < len(other_norm):
                    fallback_removable.add(value)
                    break

        if fallback_removable:
            pruned = [value for value in normalized_values if value not in fallback_removable]
            if pruned:
                normalized_values = pruned

        return normalized_values
