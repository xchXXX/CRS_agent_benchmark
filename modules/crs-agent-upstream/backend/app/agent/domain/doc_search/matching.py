"""Stable public matching helpers for doc_search result filtering."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from app.legacy.utils.emissions import expand_emissions_match_tokens, expand_emissions_shorthand


_DEFAULT_FACET_FIELD_MAP = {
    "brand": "brand",
    "series": "series",
    "model": "model",
    "doc_type": "doc_types",
    "subsystem": "subsystems",
    "ecu": "ecus",
    "supplier": "suppliers",
    "emissions": "emissions",
}

_DOC_TYPE_AMBIGUITY_EXTRA = {
    "整车电路图": ["线束图"],
}


class DocSearchResultMatcher:
    """Public matcher that preserves legacy filtering semantics."""

    _TEXT_NORMALIZATION_REPLACEMENTS = [
        ("起动机", "启动机"),
        ("起動機", "启动机"),
        ("起动", "启动"),
        ("起動", "启动"),
    ]

    def __init__(
        self,
        *,
        clarify_service: Any | None = None,
        dimension_service: Any | None = None,
    ):
        self._clarify_service = clarify_service
        self._dimension_service = dimension_service

    @property
    def facet_field_map(self) -> dict[str, str]:
        service_field_map = getattr(self._clarify_service, "facet_field_map", None)
        if isinstance(service_field_map, dict):
            return service_field_map

        if (
            self._dimension_service is not None
            and getattr(self._dimension_service, "is_loaded", False)
            and hasattr(self._dimension_service, "get_facet_field_map")
        ):
            return self._dimension_service.get_facet_field_map()

        return dict(_DEFAULT_FACET_FIELD_MAP)

    def get_facet_raw_value(self, result: Mapping[str, Any], facet: str) -> Any:
        field_name = self.facet_field_map.get(facet, facet)
        return result.get(field_name)

    def expand_emissions_raw_value(self, value: Any) -> Any:
        if value is None:
            return None

        if isinstance(value, list):
            expanded_all: list[str] = []
            for item in value:
                if not item:
                    continue
                expanded = expand_emissions_shorthand(str(item))
                expanded_all.extend(expanded if expanded else [str(item)])
            return expanded_all

        if not value:
            return value

        expanded = expand_emissions_shorthand(str(value))
        return expanded if expanded else value

    def normalize_for_compare(self, text: str) -> str:
        if not text:
            return ""

        normalized = unicodedata.normalize("NFKC", str(text)).strip().lower()
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[·•\-_/.(),，。:：;；!?！？（）【】\\[\\]{}]+", "", normalized)

        for src, dst in self._TEXT_NORMALIZATION_REPLACEMENTS:
            normalized = normalized.replace(src.lower(), dst.lower())

        return normalized

    def match_choice(self, value: Any, choice: str) -> bool:
        if not value:
            return False

        choice_lower = choice.lower()
        choice_norm = self.normalize_for_compare(choice)

        if isinstance(value, list):
            for item in value:
                item_text = str(item)
                if choice_lower in item_text.lower():
                    return True
                if choice_norm and choice_norm in self.normalize_for_compare(item_text):
                    return True
            return False

        value_text = str(value)
        if choice_lower in value_text.lower():
            return True
        return bool(choice_norm and choice_norm in self.normalize_for_compare(value_text))

    def match_emissions_choice(self, value: Any, choice: str) -> bool:
        if not value:
            return False

        wanted = set(expand_emissions_match_tokens(choice))
        if not wanted:
            return False

        if isinstance(value, list):
            for item in value:
                got = set(expand_emissions_match_tokens(str(item)))
                if got & wanted:
                    return True
            return False

        got = set(expand_emissions_match_tokens(str(value)))
        return bool(got & wanted)

    def match_doc_type_choice(self, result: Mapping[str, Any], choice: str) -> bool:
        wanted_norms = self._get_doc_type_variant_norms(choice)
        if not wanted_norms:
            return False

        doc_types = result.get("doc_types")
        if doc_types is not None:
            if isinstance(doc_types, list):
                values = [self._normalize_doc_type(value) for value in doc_types]
            else:
                values = [self._normalize_doc_type(str(doc_types))]
            for wanted in wanted_norms:
                if any(wanted in value for value in values):
                    return True

        for field_name in ("filename", "hierarchy_full"):
            raw_field = result.get(field_name)
            if not raw_field:
                continue
            field_norm = self._normalize_doc_type(str(raw_field))
            if any(wanted in field_norm for wanted in wanted_norms):
                return True

        return False

    def _get_doc_type_variant_norms(self, raw_value: Any) -> list[str]:
        raw = str(raw_value).strip()
        if not raw:
            return []

        candidates = [raw]
        if (
            self._dimension_service is not None
            and getattr(self._dimension_service, "is_loaded", False)
            and hasattr(self._dimension_service, "find_value_by_pattern")
        ):
            matched = self._dimension_service.find_value_by_pattern(raw)
            if matched and matched[0] == "doc_type":
                candidates.extend(matched[2] or [])

            values_dict = getattr(self._dimension_service, "_values", {}).get("doc_type", {})
            direct_config = values_dict.get(raw)
            if direct_config and getattr(direct_config, "patterns", None):
                candidates.extend(direct_config.patterns)

        for extra in _DOC_TYPE_AMBIGUITY_EXTRA.get(raw, []):
            candidates.append(extra)

        seen: set[str] = set()
        normalized: list[str] = []
        for candidate in candidates:
            norm = self._normalize_doc_type(candidate)
            if norm and norm not in seen:
                seen.add(norm)
                normalized.append(norm)
        return normalized

    @staticmethod
    def _normalize_doc_type(text: Any) -> str:
        return str(text).replace("起动", "启动").replace("起動", "启动").lower()

    def matches_facet(self, result: Mapping[str, Any], facet: str, choice: str) -> bool:
        if facet == "doc_type":
            return self.match_doc_type_choice(result, choice)
        value = self.get_facet_raw_value(result, facet)
        if facet == "emissions":
            return self.match_emissions_choice(self.expand_emissions_raw_value(value), choice)
        return self.match_choice(value, choice)
