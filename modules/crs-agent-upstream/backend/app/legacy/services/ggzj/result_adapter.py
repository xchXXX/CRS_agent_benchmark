"""Adapt GGZJ external search results into internal doc_search format."""

import logging
import re
from typing import Any, List, Optional, Tuple


logger = logging.getLogger(__name__)


class GgzjResultAdapter:
    """External GGZJ result -> internal search result adapter."""

    def __init__(self):
        self._dim_service = None

    def _get_dim_service(self):
        if self._dim_service is None:
            from app.legacy.services.dimension_service import dimension_service

            self._dim_service = dimension_service
        return self._dim_service

    def adapt_list(self, raw_items: List[dict], query: str) -> Tuple[List[dict], dict]:
        results = []
        for index, item in enumerate(raw_items):
            adapted = self._adapt_single(item, index, query)
            if adapted:
                results.append(adapted)

        preprocessing = self._build_preprocessing(query)
        logger.info("[GgzjAdapter] adapted %s raw items into %s internal results", len(raw_items), len(results))
        return results, preprocessing

    def _adapt_single(self, item: dict, index: int, query: str) -> Optional[dict]:
        try:
            sn = item.get("sn") or item.get("id")
            if not sn:
                return None

            filename = item.get("dataNameWs") or item.get("dataName") or ""
            file_type_str = item.get("fileType") or ""
            dimensions = self._parse_dimensions(filename)

            position_score = max(0.3, 1.0 - index * 0.005)
            match_score = self._compute_match_score(filename, query)
            score = match_score * 0.6 + position_score * 0.4

            return {
                "file_id": f"ggzj_{sn}",
                "filename": filename,
                "brand": dimensions.get("brand"),
                "series": dimensions.get("series"),
                "model": dimensions.get("model"),
                "doc_types": dimensions.get("doc_types", [file_type_str] if file_type_str else []),
                "subsystems": dimensions.get("subsystems", []),
                "ecus": dimensions.get("ecus", []),
                "emissions": dimensions.get("emissions", []),
                "eng_codes": dimensions.get("eng_codes", []),
                "suppliers": dimensions.get("suppliers", []),
                "score": score,
                "match_score": match_score,
                "pic_folder_url": None,
                "ggzj_sn": int(sn) if str(sn).isdigit() else sn,
                "ggzj_data_type": item.get("dataType"),
                "ggzj_file_no": item.get("fileNo"),
                "ggzj_file_type": file_type_str,
            }
        except Exception as exc:
            logger.warning("[GgzjAdapter] single item adaptation failed: %s item=%s", exc, item)
            return None

    @staticmethod
    def _compute_match_score(filename: str, query: str) -> float:
        if not filename or not query:
            return 0.0

        def _normalize(text: str) -> str:
            text = re.sub(r"[_\-\s\.,;:!?/\\()（）【】\[\]{}]+", "", text)
            return text.lower()

        fn_norm = _normalize(filename)
        q_norm = _normalize(query)
        if not fn_norm or not q_norm:
            return 0.0

        if q_norm in fn_norm:
            ratio = len(q_norm) / len(fn_norm)
            return 0.8 + 0.2 * ratio

        if fn_norm in q_norm:
            ratio = len(fn_norm) / len(q_norm)
            return 0.8 + 0.2 * ratio

        def _tokenize(text: str) -> set[str]:
            tokens: set[str] = set()
            for ch in text:
                if "\u4e00" <= ch <= "\u9fff":
                    tokens.add(ch)
            for seg in re.findall(r"[a-z0-9]+", text):
                if len(seg) >= 2:
                    tokens.add(seg)
            return tokens

        q_tokens = _tokenize(q_norm)
        fn_tokens = _tokenize(fn_norm)
        if not q_tokens:
            return 0.0

        hits = len(q_tokens & fn_tokens)
        return (hits / len(q_tokens)) * 0.7

    def _parse_dimensions(self, filename: str) -> dict[str, Any]:
        result = {
            "brand": None,
            "series": None,
            "model": None,
            "doc_types": [],
            "subsystems": [],
            "ecus": [],
            "emissions": [],
            "eng_codes": [],
            "suppliers": [],
        }

        dim_service = self._get_dim_service()
        if not dim_service or not dim_service.is_loaded:
            return result

        try:
            matched = dim_service.match(filename)
            brands = matched.get("brand", [])
            if brands:
                result["brand"] = brands[0]

            series_list = matched.get("series", [])
            if series_list:
                result["series"] = series_list[0]

            models = matched.get("model", [])
            if models:
                result["model"] = models[0]

            result["doc_types"] = matched.get("doc_type", [])
            result["subsystems"] = matched.get("subsystem", [])
            result["ecus"] = matched.get("ecu", [])
            result["emissions"] = matched.get("emission", [])
            result["eng_codes"] = matched.get("eng_code", [])
            result["suppliers"] = matched.get("supplier", [])
        except Exception as exc:
            logger.warning("[GgzjAdapter] dimension parse failed: %s filename=%s", exc, filename)

        return result

    def _build_preprocessing(self, query: str) -> dict[str, Any]:
        preprocessing = {
            "original_query": query,
            "normalized_query": query,
            "corrected_query": query,
            "expanded_query": query,
            "entities": {},
            "has_correction": False,
        }

        try:
            dim_service = self._get_dim_service()
            if dim_service and dim_service.is_loaded:
                preprocessing["entities"] = dim_service.match(query)

            from app.legacy.models.database import get_session_local
            from app.legacy.services.query_preprocessor import QueryPreprocessor

            db = get_session_local()()
            try:
                qr = QueryPreprocessor(db).process(query)
            finally:
                db.close()

            preprocessing["normalized_query"] = qr.normalized_query
            preprocessing["corrected_query"] = qr.corrected_query
            preprocessing["expanded_query"] = qr.expanded_query
            preprocessing["has_correction"] = qr.has_correction
            preprocessing["synonym_expansions"] = qr.synonym_expansions
            preprocessing["pinyin_corrections"] = [
                {
                    "original": item.original,
                    "corrected": item.corrected,
                    "similarity": item.similarity,
                    "is_auto": item.is_auto,
                }
                for item in (qr.pinyin_corrections or [])
            ]
            preprocessing["query_tokens"] = list(qr.query_tokens or [])
            preprocessing["token_expansions"] = dict(qr.token_expansions or {})
            preprocessing["expanded_fulltext_query"] = qr.expanded_fulltext_query
            if qr.entities:
                for facet, values in qr.entities.items():
                    if facet not in preprocessing["entities"] or not preprocessing["entities"][facet]:
                        preprocessing["entities"][facet] = values
        except Exception as exc:
            logger.warning("[GgzjAdapter] preprocessing build failed: %s", exc)

        return preprocessing
