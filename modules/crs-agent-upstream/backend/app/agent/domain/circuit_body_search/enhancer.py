"""Doc-search result enhancer for circuit-diagram body search."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
from app.agent.domain.circuit_body_search.models import CircuitBodySearchSummary
from app.agent.domain.circuit_body_search.parsed_doc_resolver import (
    ParsedCircuitDocResolver,
    compact_circuit_text,
    normalize_circuit_filename,
)
from app.agent.domain.circuit_body_search.preview_token import (
    CircuitBodyPreviewTokenCodec,
    DEFAULT_PREVIEW_TOKEN_TTL_SECONDS,
)
from app.agent.domain.circuit_body_search.reducer import CircuitBodyHitReducer
from app.agent.domain.circuit_body_search.reranker import CircuitBodyHitRerankOutput
from app.agent.domain.circuit_body_search.search_client import CircuitBodySearchClient


logger = logging.getLogger(__name__)


CircuitBodyTraceCallback = Callable[[str, dict[str, Any], str | None], None]


class CircuitBodySearchEnhancer:
    """Attach body-search summaries to top doc-search results when possible."""

    def __init__(
        self,
        *,
        config_service: Any | None = None,
        config_provider: CircuitBodySearchConfigProvider | None = None,
        resolver: ParsedCircuitDocResolver | Any | None = None,
        search_client: CircuitBodySearchClient | Any | None = None,
        reducer: CircuitBodyHitReducer | None = None,
        hit_reranker: Any | None = None,
        preview_token_codec: CircuitBodyPreviewTokenCodec | Any | None = None,
        preview_url_prefix: str = "/chat/api/circuit-body-search/preview",
    ) -> None:
        self._config_provider = config_provider or CircuitBodySearchConfigProvider(config_service=config_service)
        self._resolver = resolver or ParsedCircuitDocResolver(config_provider=self._config_provider)
        self._search_client = search_client or CircuitBodySearchClient(config_provider=self._config_provider)
        self._reducer = reducer or CircuitBodyHitReducer()
        self._hit_reranker = hit_reranker
        self._preview_token_codec = preview_token_codec or CircuitBodyPreviewTokenCodec()
        self._preview_url_prefix = preview_url_prefix.rstrip("/")

    async def enhance(
        self,
        *,
        results: list[dict[str, Any]],
        body_keyword: str,
        max_docs: int = 12,
        candidate_query: str = "",
        max_candidate_docs: int = 20,
        trace_callback: CircuitBodyTraceCallback | None = None,
    ) -> list[dict[str, Any]]:
        config = self._config_provider.load()
        keyword = str(body_keyword or "").strip()
        source_result_count = len(results or [])
        if not config.enabled:
            self._trace(
                trace_callback,
                "circuit_body_search_skipped",
                {"reason": "disabled", "keyword": keyword, "source_result_count": source_result_count},
            )
            return list(results or [])
        if not keyword:
            self._trace(
                trace_callback,
                "circuit_body_search_skipped",
                {"reason": "missing_keyword", "source_result_count": source_result_count},
            )
            return list(results or [])
        if not results:
            self._trace(
                trace_callback,
                "circuit_body_search_skipped",
                {"reason": "empty_doc_results", "keyword": keyword, "source_result_count": source_result_count},
            )
            return list(results or [])

        enhanced = [dict(item) for item in results]
        candidate_indexes = [
            index
            for index, item in enumerate(enhanced)
            if self._is_circuit_body_search_candidate_result(item)
        ]
        max_doc_count = max(int(max_docs or 0), 0)
        top_indexes = candidate_indexes[:max_doc_count] if max_doc_count else candidate_indexes
        if not top_indexes:
            self._trace(
                trace_callback,
                "circuit_body_search_skipped",
                {
                    "reason": "empty_candidate_window",
                    "keyword": keyword,
                    "result_count": len(enhanced),
                    "circuit_candidate_count": len(candidate_indexes),
                    "max_docs": max_docs,
                },
            )
            return enhanced

        index_to_filename: dict[int, str] = {}
        for index in top_indexes:
            item = enhanced[index]
            filename = normalize_circuit_filename(item.get("filename") or item.get("title"))
            if filename:
                index_to_filename[index] = filename

        if not index_to_filename:
            self._trace(
                trace_callback,
                "circuit_body_search_skipped",
                {
                    "reason": "missing_result_filenames",
                    "keyword": keyword,
                    "result_count": len(enhanced),
                    "max_docs": max_docs,
                },
            )
            return enhanced

        try:
            resolve_started_at = time.perf_counter()
            resolved = self._resolver.resolve_many(index_to_filename.values())
        except Exception as exc:
            logger.warning("Circuit body-search resolver failed: %s", exc)
            self._trace(
                trace_callback,
                "circuit_body_source_docs_resolve_failed",
                {
                    "keyword": keyword,
                    "requested_count": len(index_to_filename),
                    "filenames": self._summarize_filenames(index_to_filename.values()),
                },
                detail=str(exc),
            )
            return enhanced
        self._trace(
            trace_callback,
            "circuit_body_source_docs_resolved",
            {
                "keyword": keyword,
                "requested_count": len(index_to_filename),
                "resolved_count": len(resolved),
                "unresolved_count": max(len(index_to_filename) - len(resolved), 0),
                "elapsed_ms": self._elapsed_ms(resolve_started_at),
                "filenames": self._summarize_filenames(index_to_filename.values()),
                "resolved_documents": self._summarize_docs(resolved.values()),
            },
        )

        tasks: list[tuple[int | None, str, Any, float, asyncio.Task[dict[str, Any]]]] = []
        seen_pdf_ids: set[str] = set()
        for index, filename in index_to_filename.items():
            parsed_doc = resolved.get(filename)
            if parsed_doc is None or not parsed_doc.latest_pdf_id:
                continue
            seen_pdf_ids.add(parsed_doc.latest_pdf_id)
            search_started_at = time.perf_counter()
            task = asyncio.create_task(
                self._search_client.search(
                    pdf_id=parsed_doc.latest_pdf_id,
                    keyword=keyword,
                )
            )
            tasks.append((index, "search_result", parsed_doc, search_started_at, task))
            self._trace(
                trace_callback,
                "circuit_body_doc_search_started",
                self._doc_trace_payload(parsed_doc, keyword=keyword, source="search_result", result_index=index),
            )

        candidate_started_at = time.perf_counter()
        candidate_docs = self._search_candidate_documents(
            candidate_query=candidate_query,
            limit=max_candidate_docs,
            trace_callback=trace_callback,
        )
        self._trace(
            trace_callback,
            "circuit_candidate_docs_searched",
            {
                "keyword": keyword,
                "candidate_query": str(candidate_query or "").strip(),
                "candidate_count": len(candidate_docs),
                "elapsed_ms": self._elapsed_ms(candidate_started_at),
                "documents": self._summarize_docs(candidate_docs),
            },
        )
        for parsed_doc in candidate_docs:
            if not parsed_doc.latest_pdf_id or parsed_doc.latest_pdf_id in seen_pdf_ids:
                continue
            seen_pdf_ids.add(parsed_doc.latest_pdf_id)
            search_started_at = time.perf_counter()
            task = asyncio.create_task(
                self._search_client.search(
                    pdf_id=parsed_doc.latest_pdf_id,
                    keyword=keyword,
                )
            )
            tasks.append((None, "parsed_candidate", parsed_doc, search_started_at, task))
            self._trace(
                trace_callback,
                "circuit_body_doc_search_started",
                self._doc_trace_payload(parsed_doc, keyword=keyword, source="parsed_candidate", result_index=None),
            )

        if not tasks:
            self._trace(
                trace_callback,
                "circuit_body_search_skipped",
                {
                    "reason": "no_resolved_searchable_docs",
                    "keyword": keyword,
                    "candidate_query": str(candidate_query or "").strip(),
                    "source_result_resolved_count": len(resolved),
                    "candidate_count": len(candidate_docs),
                },
            )
            return enhanced

        hit_indexes: set[int] = set()
        attached_candidate_hit_count = 0
        doc_search_count = 0
        doc_hit_count = 0
        doc_failed_count = 0
        for index, source, parsed_doc, search_started_at, task in tasks:
            pdf_id = parsed_doc.latest_pdf_id
            try:
                raw_response = await task
                summary = self._reducer.reduce(
                    raw_response,
                    pdf_id=pdf_id,
                    keyword=keyword,
                    latest_result_path=str(getattr(parsed_doc, "latest_result_path", "") or ""),
                    document_title=str(getattr(parsed_doc, "name", "") or ""),
                )
                summary = await self._rerank_summary(
                    summary=summary,
                    document_title=str(getattr(parsed_doc, "name", "") or ""),
                    trace_callback=trace_callback,
                )
            except Exception as exc:
                logger.warning("Circuit body-search enhancement failed: %s", exc)
                raw_response = {}
                summary = CircuitBodySearchSummary(
                    status="failed",
                    reason="body_search_enhancement_failed",
                    pdf_id=pdf_id,
                    keyword=keyword,
                )

            doc_search_count += 1
            if summary.status == "hit":
                doc_hit_count += 1
            if summary.status == "failed":
                doc_failed_count += 1
            self._trace(
                trace_callback,
                "circuit_body_doc_searched",
                {
                    **self._doc_trace_payload(parsed_doc, keyword=keyword, source=source, result_index=index),
                    "status": summary.status,
                    "reason": summary.reason,
                    "raw_hit_count": summary.raw_hit_count,
                    "page_hit_count": summary.page_hit_count,
                    "region_candidate_count": summary.region_candidate_count,
                    "display_hit_count": summary.display_hit_count,
                    "more_hits_count": summary.more_hits_count,
                    "rerank_source": summary.rerank_source,
                    "best_page_number": summary.best_hit.page_number if summary.best_hit else None,
                    "best_hit_id": summary.best_hit.hit_id if summary.best_hit else "",
                    "best_snippet": summary.best_hit.snippet if summary.best_hit else "",
                    "external_status": raw_response.get("status") if isinstance(raw_response, dict) else None,
                    "elapsed_ms": self._elapsed_ms(search_started_at),
                },
            )
            if summary.status == "hit":
                self._attach_preview_fields(summary, parsed_doc, config=config, trace_callback=trace_callback)
                if index is None:
                    external_index = self._find_external_result_index_for_parsed_doc(parsed_doc, enhanced)
                    if external_index is None:
                        self._trace(
                            trace_callback,
                            "circuit_candidate_hit_skipped",
                            {
                                **self._doc_trace_payload(
                                    parsed_doc,
                                    keyword=keyword,
                                    source="parsed_candidate",
                                    result_index=None,
                                ),
                                "reason": "missing_external_document_link",
                            },
                        )
                        continue
                    enhanced[external_index]["body_search"] = summary.model_dump(mode="json")
                    hit_indexes.add(external_index)
                    attached_candidate_hit_count += 1
                else:
                    enhanced[index]["body_search"] = summary.model_dump(mode="json")
                    hit_indexes.add(index)

        self._trace(
            trace_callback,
            "circuit_body_search_completed",
            {
                "keyword": keyword,
                "candidate_query": str(candidate_query or "").strip(),
                "source_result_count": source_result_count,
                "final_result_count": len(enhanced),
                "doc_search_count": doc_search_count,
                "doc_hit_count": doc_hit_count,
                "doc_failed_count": doc_failed_count,
                "enhanced_existing_count": len(hit_indexes),
                "inserted_candidate_hit_count": 0,
                "attached_candidate_hit_count": attached_candidate_hit_count,
            },
        )
        return enhanced

    async def _rerank_summary(
        self,
        *,
        summary: CircuitBodySearchSummary,
        document_title: str,
        trace_callback: CircuitBodyTraceCallback | None = None,
    ) -> CircuitBodySearchSummary:
        if summary.status != "hit" or len(summary.top_hits) <= 1:
            return summary
        if self._hit_reranker is None:
            self._trace(
                trace_callback,
                "circuit_body_hit_rerank_skipped",
                {
                    "reason": "reranker_unavailable",
                    "keyword": summary.keyword,
                    "pdf_id": summary.pdf_id,
                    "candidate_count": len(summary.top_hits),
                },
            )
            return summary

        started_at = time.perf_counter()
        try:
            output = await self._hit_reranker.rerank(
                query=summary.keyword,
                document_title=document_title,
                candidates=summary.top_hits,
            )
        except Exception as exc:
            logger.warning("Circuit body-search hit reranker failed: %s", exc)
            self._trace(
                trace_callback,
                "circuit_body_hit_rerank_failed",
                {
                    "keyword": summary.keyword,
                    "pdf_id": summary.pdf_id,
                    "candidate_count": len(summary.top_hits),
                    "elapsed_ms": self._elapsed_ms(started_at),
                },
                detail=str(exc),
            )
            return summary

        if not isinstance(output, CircuitBodyHitRerankOutput) or not output.ranked_candidates:
            self._trace(
                trace_callback,
                "circuit_body_hit_rerank_skipped",
                {
                    "reason": "empty_rerank_output",
                    "keyword": summary.keyword,
                    "pdf_id": summary.pdf_id,
                    "candidate_count": len(summary.top_hits),
                    "elapsed_ms": self._elapsed_ms(started_at),
                },
            )
            return summary

        by_key: dict[str, Any] = {}
        for hit in summary.top_hits:
            if hit.candidate_id:
                by_key[hit.candidate_id] = hit
            by_key[hit.hit_id] = hit

        ordered = []
        seen: set[str] = set()
        for item in sorted(output.ranked_candidates, key=lambda candidate: int(candidate.rank or 1_000_000)):
            key = str(item.candidate_id or "").strip()
            hit = by_key.get(key)
            if hit is None or hit.hit_id in seen:
                continue
            hit.confidence = item.confidence
            hit.reason = str(item.reason or "").strip() or hit.reason
            ordered.append(hit)
            seen.add(hit.hit_id)

        for hit in summary.top_hits:
            if hit.hit_id not in seen:
                ordered.append(hit)
                seen.add(hit.hit_id)

        if not ordered:
            return summary

        for index, hit in enumerate(ordered, start=1):
            hit.display_rank = index
        summary.top_hits = ordered
        summary.best_hit = ordered[0]
        summary.display_hit_count = len(ordered)
        summary.rerank_source = "llm"
        self._trace(
            trace_callback,
            "circuit_body_hit_reranked",
            {
                "keyword": summary.keyword,
                "pdf_id": summary.pdf_id,
                "candidate_count": len(ordered),
                "best_hit_id": summary.best_hit.hit_id if summary.best_hit else "",
                "best_candidate_id": summary.best_hit.candidate_id if summary.best_hit else "",
                "elapsed_ms": self._elapsed_ms(started_at),
            },
        )
        return summary

    def _search_candidate_documents(
        self,
        *,
        candidate_query: str,
        limit: int,
        trace_callback: CircuitBodyTraceCallback | None = None,
    ) -> list[Any]:
        query = str(candidate_query or "").strip()
        if not query or limit <= 0 or not hasattr(self._resolver, "search_candidates"):
            self._trace(
                trace_callback,
                "circuit_candidate_docs_search_skipped",
                {
                    "reason": "missing_candidate_query_or_resolver",
                    "candidate_query": query,
                    "limit": limit,
                    "resolver_supports_candidates": hasattr(self._resolver, "search_candidates"),
                },
            )
            return []
        try:
            return list(self._resolver.search_candidates(query, limit=limit))
        except Exception as exc:
            logger.warning("Circuit parsed-document candidate lookup failed: %s", exc)
            self._trace(
                trace_callback,
                "circuit_candidate_docs_search_failed",
                {"candidate_query": query, "limit": limit},
                detail=str(exc),
            )
            return []

    @staticmethod
    def _find_external_result_index_for_parsed_doc(
        parsed_doc: Any,
        results: list[dict[str, Any]],
    ) -> int | None:
        item_id = str(getattr(parsed_doc, "item_id", "") or "").strip()
        parsed_name = normalize_circuit_filename(getattr(parsed_doc, "name", ""))
        parsed_compact = compact_circuit_text(parsed_name)

        for index, result in enumerate(results):
            if not CircuitBodySearchEnhancer._is_attachable_external_circuit_result(result):
                continue
            ref_file_id = str(result.get("ref_file_id") or "").strip()
            parent_id = str(result.get("parent_id") or "").strip()
            file_id = str(result.get("file_id") or "").strip()
            if item_id and item_id in {ref_file_id, parent_id, file_id}:
                return index

        for index, result in enumerate(results):
            if not CircuitBodySearchEnhancer._is_attachable_external_circuit_result(result):
                continue
            result_name = normalize_circuit_filename(result.get("filename") or result.get("title"))
            if parsed_name and result_name == parsed_name:
                return index

        for index, result in enumerate(results):
            if not CircuitBodySearchEnhancer._is_attachable_external_circuit_result(result):
                continue
            result_name = normalize_circuit_filename(result.get("filename") or result.get("title"))
            result_compact = compact_circuit_text(result_name)
            if parsed_compact and result_compact and (
                parsed_compact in result_compact or result_compact in parsed_compact
            ):
                return index

        return None

    @staticmethod
    def _normalize_data_type(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        return None

    @staticmethod
    def _is_circuit_body_search_candidate_result(result: dict[str, Any]) -> bool:
        data_type = CircuitBodySearchEnhancer._normalize_data_type(result.get("ggzj_data_type"))
        if data_type == 3:
            return True

        text_parts: list[str] = []
        for key in ("filename", "title", "physical_path", "hierarchy_full", "ggzj_file_type", "file_type"):
            value = result.get(key)
            if value:
                text_parts.append(str(value))
        for key in ("doc_types", "tags"):
            value = result.get(key)
            if isinstance(value, dict):
                text_parts.extend(str(item) for item in value.values() if item)
            elif isinstance(value, (list, tuple, set)):
                text_parts.extend(str(item) for item in value if item)
            elif value:
                text_parts.append(str(value))

        text = " ".join(text_parts)
        if not text:
            return False
        return any(marker in text for marker in ("电路图", "线束图", "针脚定义", "针脚图"))

    @staticmethod
    def _has_external_document_link(result: dict[str, Any]) -> bool:
        return bool(
            result.get("pic_folder_url")
            or result.get("ggzj_sn")
        )

    @staticmethod
    def _is_attachable_external_circuit_result(result: dict[str, Any]) -> bool:
        return (
            CircuitBodySearchEnhancer._is_circuit_body_search_candidate_result(result)
            and CircuitBodySearchEnhancer._has_external_document_link(result)
        )

    def _attach_preview_fields(
        self,
        summary: CircuitBodySearchSummary,
        parsed_doc: Any,
        *,
        config: Any,
        trace_callback: CircuitBodyTraceCallback | None = None,
    ) -> None:
        source_pdf_url = str(getattr(parsed_doc, "url_raw_sample", "") or "").strip()
        if source_pdf_url:
            summary.source_pdf_url = source_pdf_url
            summary.viewer_url_type = "raw_pdf"

        if summary.best_hit is None and not summary.top_hits:
            return

        has_preview_source = bool(source_pdf_url or str(getattr(parsed_doc, "latest_result_path", "") or "").strip())
        hits_for_preview = summary.top_hits or ([summary.best_hit] if summary.best_hit is not None else [])
        if not has_preview_source or not any(hit.highlight_boxes_px for hit in hits_for_preview):
            self._trace(
                trace_callback,
                "circuit_preview_token_skipped",
                {
                    **self._doc_trace_payload(parsed_doc, keyword=summary.keyword, source="body_hit", result_index=None),
                    "reason": "missing_preview_source_or_boxes",
                    "has_source_pdf_url": bool(source_pdf_url),
                    "has_result_path": bool(str(getattr(parsed_doc, "latest_result_path", "") or "").strip()),
                    "has_highlight_boxes": any(hit.highlight_boxes_px for hit in hits_for_preview),
                },
            )
            return

        created_count = 0
        for hit in hits_for_preview:
            if not hit.highlight_boxes_px:
                continue
            try:
                token = self._preview_token_codec.encode(
                    {
                        "pdf_id": summary.pdf_id,
                        "filename": str(getattr(parsed_doc, "name", "") or ""),
                        "keyword": summary.keyword,
                        "hit_id": hit.hit_id,
                        "latest_result_path": str(getattr(parsed_doc, "latest_result_path", "") or ""),
                        "source_pdf_url": source_pdf_url,
                        "page_index": hit.page_index,
                        "highlight_boxes_px": hit.highlight_boxes_px,
                    },
                    ttl_seconds=int(
                        getattr(config, "preview_token_ttl_seconds", DEFAULT_PREVIEW_TOKEN_TTL_SECONDS)
                        or DEFAULT_PREVIEW_TOKEN_TTL_SECONDS
                    ),
                )
            except Exception as exc:
                logger.warning("Circuit body-search preview token build failed: %s", exc)
                self._trace(
                    trace_callback,
                    "circuit_preview_token_failed",
                    {
                        **self._doc_trace_payload(parsed_doc, keyword=summary.keyword, source="body_hit", result_index=None),
                        "hit_id": hit.hit_id,
                        "candidate_id": hit.candidate_id,
                    },
                    detail=str(exc),
                )
                continue

            hit.viewer_token = token
            hit.preview_image_url = f"{self._preview_url_prefix}/{token}"
            if summary.best_hit is not None and hit.hit_id == summary.best_hit.hit_id:
                summary.viewer_token = token
            created_count += 1

        self._trace(
            trace_callback,
            "circuit_preview_token_created",
            {
                **self._doc_trace_payload(parsed_doc, keyword=summary.keyword, source="body_hit", result_index=None),
                "page_index": summary.best_hit.page_index if summary.best_hit else None,
                "page_number": summary.best_hit.page_number if summary.best_hit else None,
                "box_count": len(summary.best_hit.highlight_boxes_px) if summary.best_hit else 0,
                "created_token_count": created_count,
                "has_viewer_token": bool(summary.viewer_token),
                "has_source_pdf_url": bool(source_pdf_url),
                "has_result_path": bool(str(getattr(parsed_doc, "latest_result_path", "") or "").strip()),
            },
        )

    @staticmethod
    def _trace(
        trace_callback: CircuitBodyTraceCallback | None,
        event_type: str,
        payload: dict[str, Any],
        detail: str | None = None,
    ) -> None:
        if trace_callback is None:
            return
        try:
            trace_callback(event_type, payload, detail)
        except Exception:
            logger.debug("Circuit body-search trace callback failed", exc_info=True)

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _summarize_filenames(values: Any, *, limit: int = 8) -> list[str]:
        return [str(value or "") for value in list(values or [])[:limit]]

    @staticmethod
    def _summarize_docs(docs: Any, *, limit: int = 8) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for doc in list(docs or [])[:limit]:
            summaries.append(
                {
                    "item_id": str(getattr(doc, "item_id", "") or ""),
                    "pdf_id": str(getattr(doc, "latest_pdf_id", "") or ""),
                    "filename": str(getattr(doc, "name", "") or ""),
                    "has_source_pdf_url": bool(str(getattr(doc, "url_raw_sample", "") or "").strip()),
                    "has_result_path": bool(str(getattr(doc, "latest_result_path", "") or "").strip()),
                }
            )
        return summaries

    @staticmethod
    def _doc_trace_payload(
        parsed_doc: Any,
        *,
        keyword: str,
        source: str,
        result_index: int | None,
    ) -> dict[str, Any]:
        return {
            "source": source,
            "result_index": result_index,
            "keyword": keyword,
            "item_id": str(getattr(parsed_doc, "item_id", "") or ""),
            "pdf_id": str(getattr(parsed_doc, "latest_pdf_id", "") or ""),
            "filename": str(getattr(parsed_doc, "name", "") or ""),
            "has_source_pdf_url": bool(str(getattr(parsed_doc, "url_raw_sample", "") or "").strip()),
            "has_result_path": bool(str(getattr(parsed_doc, "latest_result_path", "") or "").strip()),
        }
