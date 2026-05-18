"""Summary builders for doc_search results."""

from typing import Any

from app.agent.domain.doc_search.models import DocSearchResultSummary


class DocSearchSummaryBuilder:
    """Build human-facing summary fields while preserving legacy wording."""

    def __init__(self, *, dimension_service: Any | None = None):
        self._dimension_service = dimension_service

    def build_summary_query(self, original_query: str, filters: dict[str, Any]) -> str:
        if not filters:
            return original_query

        parts: list[str] = []
        for key in ["brand", "series", "model", "doc_type", "subsystem", "ecu"]:
            value = filters.get(key)
            if value in (None, ""):
                continue
            display_value = str(value)
            if key == "doc_type":
                display_value = self._resolve_doc_type_display_value(original_query, display_value)
            parts.append(display_value)

        if not parts:
            return original_query

        original_lower = str(original_query or "").lower()
        for keyword in ["电路图", "线束图", "针脚", "维修手册", "诊断手册", "原理图"]:
            if keyword in original_lower and "doc_type" not in filters:
                parts.append(keyword)
                break

        return " ".join(parts)

    def build_summary_text(
        self,
        *,
        original_query: str,
        filters: dict[str, Any],
        total_hits: int,
        returned_count: int,
    ) -> tuple[str, str]:
        summary_query = self.build_summary_query(original_query, filters)
        summary_text = f"找到 {int(total_hits)} 个「{summary_query}」相关文档"
        if total_hits > returned_count:
            summary_text += f"，当前展示 {returned_count} 条"
        return summary_query, summary_text

    def build_result_summary(
        self,
        *,
        original_query: str,
        filters: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> DocSearchResultSummary:
        question_parts = [original_query]
        filter_parts = [
            str(filters[key])
            for key in ("brand", "series", "model")
            if filters.get(key) not in (None, "")
        ]
        if filter_parts:
            question_parts.append(f"（{' '.join(filter_parts)}）")
        question = "".join(question_parts)

        result_count = len(results)
        if result_count > 0:
            top_titles = [str(item.get("filename", "")) for item in results[:2]]
            preview = f"包含：{', '.join(top_titles)}"
            if result_count > 2:
                preview += " 等"
        else:
            preview = "未找到相关文档"

        return DocSearchResultSummary(
            question=question,
            result_type="search",
            result_count=result_count,
            preview=preview,
            display_title=f"搜索：{question}",
            display_subtitle=f"找到 {result_count} 个文档",
            can_collapse=result_count > 3,
        )

    def _resolve_doc_type_display_value(self, original_query: str, canonical_value: str) -> str:
        canonical_text = str(canonical_value or "").strip()
        if not canonical_text:
            return canonical_text

        original_text = str(original_query or "").strip()
        if not original_text:
            return canonical_text

        variant_candidates: list[str] = [canonical_text]
        try:
            if self._dimension_service is not None and getattr(self._dimension_service, "is_loaded", False):
                matched = self._dimension_service.find_value_by_pattern(canonical_text)
                if matched and matched[0] == "doc_type":
                    variant_candidates.extend(matched[2] or [])
        except Exception:
            return canonical_text

        original_norm = self._normalize_doc_type(original_text)
        best_match = ""
        best_len = -1
        seen_norm: set[str] = set()
        for variant in variant_candidates:
            candidate = str(variant or "").strip()
            if not candidate:
                continue
            candidate_norm = self._normalize_doc_type(candidate)
            if not candidate_norm or candidate_norm in seen_norm:
                continue
            seen_norm.add(candidate_norm)
            if candidate_norm in original_norm and len(candidate_norm) > best_len:
                best_match = candidate
                best_len = len(candidate_norm)
        return best_match or canonical_text

    @staticmethod
    def _normalize_doc_type(text: str) -> str:
        return str(text).replace("起动", "启动").replace("起動", "启动").lower()
