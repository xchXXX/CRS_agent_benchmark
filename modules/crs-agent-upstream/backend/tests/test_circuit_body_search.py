import asyncio
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.agent.domain.circuit_body_search.config import CircuitBodySearchConfigProvider
from app.agent.domain.circuit_body_search.enhancer import CircuitBodySearchEnhancer
from app.agent.domain.circuit_body_search.keyword import resolve_circuit_body_keyword
from app.agent.domain.circuit_body_search.models import ParsedCircuitDocument
from app.agent.domain.circuit_body_search.parsed_doc_resolver import normalize_circuit_filename
from app.agent.domain.circuit_body_search.preview_renderer import CircuitBodyPreviewRenderer
from app.agent.domain.circuit_body_search.preview_token import (
    CircuitBodyPreviewTokenCodec,
    CircuitBodyPreviewTokenError,
    CircuitBodyPreviewTokenPayload,
)
from app.agent.domain.circuit_body_search.reducer import CircuitBodyHitReducer
from app.agent.domain.circuit_body_search.reranker import CircuitBodyHitRerankItem, CircuitBodyHitRerankOutput
from app.agent.domain.circuit_body_search.viewer_points import CircuitBodyViewerPointLocator
from app.core.config import Settings
from app.main import create_app


class FakeConfigProvider:
    def __init__(self, enabled=True):
        self._enabled = enabled

    def load(self):
        return SimpleNamespace(enabled=self._enabled)


class FakeResolver:
    def __init__(self, mapping, candidates=None):
        self.mapping = mapping
        self.candidates = list(candidates or [])
        self.calls = []
        self.candidate_calls = []

    def resolve_many(self, filenames):
        self.calls.append(list(filenames))
        return self.mapping

    def search_candidates(self, query, *, limit=20):
        self.candidate_calls.append({"query": query, "limit": limit})
        return self.candidates[:limit]


class FakeSearchClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def search(self, *, pdf_id: str, keyword: str):
        self.calls.append({"pdf_id": pdf_id, "keyword": keyword})
        return self.responses.get(pdf_id, {"data": {"results": [], "total_matches": 0}})


class FakeCircuitConfigService:
    def __init__(self, values):
        self._values = dict(values)

    def get(self, key, default=None):
        return self._values.get(key, default)


class FakePreviewTokenCodec:
    def __init__(self):
        self.calls = []

    def encode(self, payload, *, ttl_seconds: int):
        self.calls.append({"payload": dict(payload), "ttl_seconds": ttl_seconds})
        return "preview_token"


class UniquePreviewTokenCodec:
    def __init__(self):
        self.calls = []

    def encode(self, payload, *, ttl_seconds: int):
        self.calls.append({"payload": dict(payload), "ttl_seconds": ttl_seconds})
        return f"token_{payload['hit_id']}"


class FakeHitReranker:
    def __init__(self, candidate_ids):
        self.candidate_ids = list(candidate_ids)
        self.calls = []

    async def rerank(self, *, query, document_title, candidates):
        self.calls.append(
            {
                "query": query,
                "document_title": document_title,
                "candidate_ids": [candidate.candidate_id for candidate in candidates],
            }
        )
        return CircuitBodyHitRerankOutput(
            ranked_candidates=[
                CircuitBodyHitRerankItem(
                    candidate_id=candidate_id,
                    rank=index,
                    confidence="high" if index == 1 else "medium",
                    reason=f"rank {index}",
                )
                for index, candidate_id in enumerate(self.candidate_ids, start=1)
            ]
        )


def test_normalize_circuit_filename_removes_pdf_extension_only():
    assert normalize_circuit_filename(" 东风天锦整车电路图.PDF ") == "东风天锦整车电路图"
    assert normalize_circuit_filename("东风天锦整车电路图.pdf") == "东风天锦整车电路图"
    assert normalize_circuit_filename("东风天锦整车电路图") == "东风天锦整车电路图"


def test_circuit_config_provider_falls_back_to_env_when_hot_config_string_is_empty():
    provider = CircuitBodySearchConfigProvider(
        config_service=FakeCircuitConfigService({"circuit_diagram_body_search_pg_password": ""}),
        settings=Settings(circuit_diagram_body_search_pg_password="env-password"),
    )

    config = provider.load()

    assert config.pg_password == "env-password"
    assert config.parsed_db_configured is True


def test_resolve_circuit_body_keyword_prefers_explicit_field():
    keyword = resolve_circuit_body_keyword(
        search_data={
            "body_keyword": "涡轮增压器",
            "original_query": "东风天锦涡轮增压器电路图",
        },
        fallback_query="fallback",
    )

    assert keyword == "涡轮增压器"


def test_reducer_selects_highest_hit_count_page_and_first_reading_order():
    reducer = CircuitBodyHitReducer()

    summary = reducer.reduce(
        {
            "code": 200,
            "data": {
                "total_matches": 4,
                "results": [
                    {
                        "match_id": "p2_late",
                        "page_index": 2,
                        "reading_order": 9,
                        "matched_text": "涡轮增压器 B",
                        "bounding_box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                    },
                    {
                        "match_id": "p1_only",
                        "page_index": 1,
                        "reading_order": 1,
                        "matched_text": "涡轮增压器 A",
                        "bounding_box": {"x_min": 5, "y_min": 6, "x_max": 7, "y_max": 8},
                    },
                    {
                        "match_id": "p2_first",
                        "page_index": 2,
                        "reading_order": 2,
                        "matched_text": "涡轮增压器 C",
                        "context": "执行器附近",
                        "bounding_box": {"x_min": 11, "y_min": 12, "x_max": 13, "y_max": 14},
                    },
                ],
            },
        },
        pdf_id="pdf_1",
        keyword="涡轮增压器",
    )

    assert summary.status == "hit"
    assert summary.raw_hit_count == 4
    assert summary.page_hit_count == 2
    assert summary.region_candidate_count == 2
    assert summary.display_hit_count == 2
    assert summary.best_hit is not None
    assert summary.best_hit.hit_id == "p2_first"
    assert summary.best_hit.page_number == 3
    assert summary.best_hit.highlight_boxes_px == [[11.0, 12.0, 13.0, 14.0], [1.0, 2.0, 3.0, 4.0]]
    assert [hit.page_number for hit in summary.top_hits] == [3, 2]


def test_reducer_builds_region_candidates_with_nearby_ocr_evidence(tmp_path):
    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 0,
                        "elements": [
                            {
                                "text_content": "电子油门踏板 APP1 APP2 5V 电源 搭铁",
                                "reading_order": 3,
                                "bounding_box": [90, 90, 520, 150],
                            },
                            {
                                "text_content": "雨刮开关",
                                "reading_order": 20,
                                "bounding_box": [2500, 2500, 2700, 2600],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = CircuitBodyHitReducer().reduce(
        {
            "data": {
                "total_matches": 2,
                "results": [
                    {
                        "match_id": "pedal",
                        "page_index": 0,
                        "reading_order": 4,
                        "matched_text": "油门踏板",
                        "bounding_box": {"x_min": 100, "y_min": 100, "x_max": 130, "y_max": 130},
                    },
                    {
                        "match_id": "wiper",
                        "page_index": 0,
                        "reading_order": 21,
                        "matched_text": "踏板",
                        "bounding_box": {"x_min": 2500, "y_min": 2500, "x_max": 2530, "y_max": 2530},
                    },
                ],
            }
        },
        pdf_id="pdf_1",
        keyword="油门踏板",
        latest_result_path=str(result_json),
        document_title="东风天锦整车电路图",
    )

    assert summary.status == "hit"
    assert summary.region_candidate_count == 2
    assert summary.top_hits[0].hit_id == "pedal"
    assert "APP1" in summary.top_hits[0].nearby_ocr_text
    assert summary.top_hits[0].confidence == "high"


def test_enhancer_only_attaches_hit_summary_to_top_docs():
    parsed_a = ParsedCircuitDocument(
        item_id="1",
        name="A电路图",
        latest_pdf_id="pdf_a",
        latest_result_path="/a.json",
        url_raw_sample="https://example.com/a.pdf",
    )
    parsed_b = ParsedCircuitDocument(
        item_id="2",
        name="B电路图",
        latest_pdf_id="pdf_b",
        latest_result_path="/b.json",
        url_raw_sample="https://example.com/b.pdf",
    )
    resolver = FakeResolver({"A电路图": parsed_a, "B电路图": parsed_b})
    client = FakeSearchClient(
        {
            "pdf_a": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_a",
                            "page_index": 0,
                            "reading_order": 1,
                            "matched_text": "涡轮增压器",
                            "bounding_box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                        }
                    ],
                }
            },
            "pdf_b": {"data": {"total_matches": 0, "results": []}},
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[
                {"file_id": "a", "filename": "A电路图.PDF", "ggzj_data_type": 3},
                {"file_id": "b", "filename": "B电路图", "ggzj_data_type": 3},
                {"file_id": "c", "filename": "C电路图", "ggzj_data_type": 3},
                {"file_id": "d", "filename": "D电路图", "ggzj_data_type": 3},
            ],
            body_keyword="涡轮增压器",
            max_docs=3,
        )
    )

    assert resolver.calls == [["A电路图", "B电路图", "C电路图"]]
    assert client.calls == [
        {"pdf_id": "pdf_a", "keyword": "涡轮增压器"},
        {"pdf_id": "pdf_b", "keyword": "涡轮增压器"},
    ]
    assert enhanced[0]["body_search"]["status"] == "hit"
    assert enhanced[0]["body_search"]["source_pdf_url"] == "https://example.com/a.pdf"
    assert enhanced[0]["body_search"]["viewer_url_type"] == "raw_pdf"
    assert enhanced[0]["body_search"]["viewer_token"] == "preview_token"
    assert enhanced[0]["body_search"]["best_hit"]["preview_image_url"] == (
        "/chat/api/circuit-body-search/preview/preview_token"
    )
    assert "body_search" not in enhanced[1]
    assert "body_search" not in enhanced[3]


def test_enhancer_searches_candidate_window_without_reordering_later_body_hit():
    parsed_d = ParsedCircuitDocument(
        item_id="4",
        name="D电路图",
        latest_pdf_id="pdf_d",
        latest_result_path="/d.json",
        url_raw_sample="https://example.com/d.pdf",
    )
    resolver = FakeResolver({"D电路图": parsed_d})
    client = FakeSearchClient(
        {
            "pdf_d": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_d",
                            "page_index": 2,
                            "reading_order": 1,
                            "matched_text": "油门踏板",
                            "bounding_box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                        }
                    ],
                }
            }
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[
                {"file_id": "a", "filename": "A电路图", "ggzj_data_type": 3},
                {"file_id": "b", "filename": "B电路图", "ggzj_data_type": 3},
                {"file_id": "c", "filename": "C电路图", "ggzj_data_type": 3},
                {"file_id": "d", "filename": "D电路图", "ggzj_data_type": 3},
            ],
            body_keyword="油门踏板",
            max_docs=12,
        )
    )

    assert resolver.calls == [["A电路图", "B电路图", "C电路图", "D电路图"]]
    assert client.calls == [{"pdf_id": "pdf_d", "keyword": "油门踏板"}]
    assert [item["file_id"] for item in enhanced] == ["a", "b", "c", "d"]
    assert enhanced[3]["body_search"]["status"] == "hit"


def test_enhancer_only_searches_data_type_3_results_and_skips_non_circuit_docs():
    parsed_a = ParsedCircuitDocument(
        item_id="1",
        name="A维修资料",
        latest_pdf_id="pdf_a",
        latest_result_path="/a.json",
        url_raw_sample="https://example.com/a.pdf",
    )
    parsed_b = ParsedCircuitDocument(
        item_id="2",
        name="B电路图",
        latest_pdf_id="pdf_b",
        latest_result_path="/b.json",
        url_raw_sample="https://example.com/b.pdf",
    )
    resolver = FakeResolver({"A维修资料": parsed_a, "B电路图": parsed_b})
    client = FakeSearchClient(
        {
            "pdf_a": {"data": {"total_matches": 1, "results": []}},
            "pdf_b": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_b",
                            "page_index": 0,
                            "reading_order": 1,
                            "matched_text": "油门踏板",
                            "bounding_box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                        }
                    ],
                }
            },
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[
                {"file_id": "a", "filename": "A维修资料", "ggzj_data_type": 2},
                {"file_id": "b", "filename": "B电路图", "ggzj_data_type": 3},
            ],
            body_keyword="油门踏板",
            max_docs=2,
        )
    )

    assert resolver.calls == [["B电路图"]]
    assert client.calls == [{"pdf_id": "pdf_b", "keyword": "油门踏板"}]
    assert "body_search" not in enhanced[0]
    assert enhanced[1]["body_search"]["status"] == "hit"


def test_enhancer_searches_wps_backed_circuit_documents_by_title():
    parsed = ParsedCircuitDocument(
        item_id="1",
        name="东风天锦整车电路图",
        latest_pdf_id="pdf_circuit",
        latest_result_path="/circuit.json",
        url_raw_sample="https://example.com/circuit.pdf",
    )
    resolver = FakeResolver({"东风天锦整车电路图": parsed})
    client = FakeSearchClient(
        {
            "pdf_circuit": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_circuit",
                            "page_index": 1,
                            "reading_order": 1,
                            "matched_text": "BCM",
                            "bounding_box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                        }
                    ],
                }
            }
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[
                {
                    "file_id": "wps_circuit",
                    "filename": "东风天锦整车电路图",
                    "ggzj_data_type": 2,
                    "ggzj_file_type": "zip",
                }
            ],
            body_keyword="BCM",
            max_docs=1,
        )
    )

    assert resolver.calls == [["东风天锦整车电路图"]]
    assert client.calls == [{"pdf_id": "pdf_circuit", "keyword": "BCM"}]
    assert enhanced[0]["body_search"]["status"] == "hit"
    assert enhanced[0]["body_search"]["best_hit"]["page_number"] == 2


def test_enhancer_searches_circuit_result_after_many_non_circuit_docs():
    parsed_target = ParsedCircuitDocument(
        item_id="target",
        name="目标电路图",
        latest_pdf_id="pdf_target",
        latest_result_path="/target.json",
        url_raw_sample="https://example.com/target.pdf",
    )
    resolver = FakeResolver({"目标电路图": parsed_target})
    client = FakeSearchClient(
        {
            "pdf_target": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_target",
                            "page_index": 4,
                            "reading_order": 1,
                            "matched_text": "BCM",
                            "bounding_box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                        }
                    ],
                }
            }
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )
    results = [
        {"file_id": f"doc_{index}", "filename": f"普通资料{index}", "ggzj_data_type": 2}
        for index in range(12)
    ]
    results.append({"file_id": "target", "filename": "目标电路图", "ggzj_data_type": 3})

    enhanced = asyncio.run(
        enhancer.enhance(
            results=results,
            body_keyword="BCM",
            max_docs=len(results),
        )
    )

    assert resolver.calls == [["目标电路图"]]
    assert client.calls == [{"pdf_id": "pdf_target", "keyword": "BCM"}]
    assert enhanced[-1]["body_search"]["status"] == "hit"
    assert enhanced[-1]["body_search"]["best_hit"]["page_number"] == 5


def test_enhancer_reranks_top_hits_and_creates_preview_tokens_for_each():
    parsed = ParsedCircuitDocument(
        item_id="1",
        name="A电路图",
        latest_pdf_id="pdf_a",
        latest_result_path="/a.json",
        url_raw_sample="https://example.com/a.pdf",
    )
    resolver = FakeResolver({"A电路图": parsed})
    client = FakeSearchClient(
        {
            "pdf_a": {
                "data": {
                    "total_matches": 2,
                    "results": [
                        {
                            "match_id": "first",
                            "page_index": 0,
                            "reading_order": 1,
                            "matched_text": "油门踏板",
                            "bounding_box": {"x_min": 10, "y_min": 20, "x_max": 30, "y_max": 40},
                        },
                        {
                            "match_id": "second",
                            "page_index": 1,
                            "reading_order": 1,
                            "matched_text": "电子油门踏板",
                            "context": "APP1 APP2",
                            "bounding_box": {"x_min": 110, "y_min": 120, "x_max": 130, "y_max": 140},
                        },
                    ],
                }
            }
        }
    )
    codec = UniquePreviewTokenCodec()
    reranker = FakeHitReranker(["pdf_a:p1:r2", "pdf_a:p0:r1"])
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        hit_reranker=reranker,
        preview_token_codec=codec,
    )

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[{"file_id": "a", "filename": "A电路图", "ggzj_data_type": 3}],
            body_keyword="油门踏板",
            max_docs=1,
        )
    )

    body_search = enhanced[0]["body_search"]
    assert reranker.calls[0]["query"] == "油门踏板"
    assert body_search["rerank_source"] == "llm"
    assert body_search["best_hit"]["hit_id"] == "second"
    assert [hit["hit_id"] for hit in body_search["top_hits"]] == ["second", "first"]
    assert body_search["top_hits"][0]["viewer_token"] == "token_second"
    assert body_search["top_hits"][1]["viewer_token"] == "token_first"
    assert body_search["viewer_token"] == "token_second"
    assert [call["payload"]["hit_id"] for call in codec.calls] == ["second", "first"]


def test_enhancer_attaches_parsed_candidate_body_hit_to_existing_external_result():
    parsed_extra = ParsedCircuitDocument(
        item_id="extra",
        name="东风天锦_D530改型驾驶室_整车电路图",
        latest_pdf_id="pdf_extra",
        latest_result_path="/extra.json",
        url_raw_sample="https://example.com/extra.pdf",
    )
    resolver = FakeResolver({}, candidates=[parsed_extra])
    client = FakeSearchClient(
        {
            "pdf_extra": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_extra",
                            "page_index": 3,
                            "reading_order": 1,
                            "matched_text": "电子油门踏板",
                            "bounding_box": {"x_min": 10, "y_min": 20, "x_max": 30, "y_max": 40},
                        }
                    ],
                }
            }
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )
    trace_events = []

    def collect_trace(event_type, payload, detail=None):
        trace_events.append({"event_type": event_type, "payload": payload, "detail": detail})

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[
                {
                    "file_id": "a",
                    "filename": "东风天锦_D530改型驾驶室_整车电路图_彩绘版",
                    "pic_folder_url": "https://example.com/external-doc",
                    "ggzj_data_type": 3,
                    "score": 0.42,
                }
            ],
            body_keyword="油门踏板",
            candidate_query="东风天锦整车电路图",
            max_candidate_docs=20,
            trace_callback=collect_trace,
        )
    )

    assert resolver.candidate_calls == [{"query": "东风天锦整车电路图", "limit": 20}]
    assert client.calls == [{"pdf_id": "pdf_extra", "keyword": "油门踏板"}]
    assert len(enhanced) == 1
    assert enhanced[0]["file_id"] == "a"
    assert enhanced[0]["filename"] == "东风天锦_D530改型驾驶室_整车电路图_彩绘版"
    assert enhanced[0]["pic_folder_url"] == "https://example.com/external-doc"
    assert enhanced[0]["ggzj_data_type"] == 3
    assert enhanced[0]["score"] == 0.42
    assert enhanced[0]["body_search"]["status"] == "hit"
    assert enhanced[0]["body_search"]["best_hit"]["page_number"] == 4
    event_types = [event["event_type"] for event in trace_events]
    assert "circuit_body_source_docs_resolved" in event_types
    assert "circuit_candidate_docs_searched" in event_types
    assert "circuit_body_doc_search_started" in event_types
    assert "circuit_body_doc_searched" in event_types
    assert "circuit_preview_token_created" in event_types
    assert "circuit_body_search_completed" in event_types
    doc_searched = next(event for event in trace_events if event["event_type"] == "circuit_body_doc_searched")
    assert doc_searched["payload"]["source"] == "parsed_candidate"
    assert doc_searched["payload"]["keyword"] == "油门踏板"
    assert doc_searched["payload"]["raw_hit_count"] == 1
    assert doc_searched["payload"]["page_hit_count"] == 1
    completed = next(event for event in trace_events if event["event_type"] == "circuit_body_search_completed")
    assert completed["payload"]["inserted_candidate_hit_count"] == 0
    assert completed["payload"]["attached_candidate_hit_count"] == 1


def test_enhancer_skips_parsed_candidate_body_hit_without_external_link():
    parsed_extra = ParsedCircuitDocument(
        item_id="extra",
        name="东风天锦_D530改型驾驶室_整车电路图",
        latest_pdf_id="pdf_extra",
        latest_result_path="/extra.json",
        url_raw_sample="https://example.com/extra.pdf",
    )
    resolver = FakeResolver({}, candidates=[parsed_extra])
    client = FakeSearchClient(
        {
            "pdf_extra": {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_extra",
                            "page_index": 3,
                            "reading_order": 1,
                            "matched_text": "电子油门踏板",
                            "bounding_box": {"x_min": 10, "y_min": 20, "x_max": 30, "y_max": 40},
                        }
                    ],
                }
            }
        }
    )
    enhancer = CircuitBodySearchEnhancer(
        config_provider=FakeConfigProvider(),
        resolver=resolver,
        search_client=client,
        preview_token_codec=FakePreviewTokenCodec(),
    )
    trace_events = []

    def collect_trace(event_type, payload, detail=None):
        trace_events.append({"event_type": event_type, "payload": payload, "detail": detail})

    enhanced = asyncio.run(
        enhancer.enhance(
            results=[
                {
                    "file_id": "a",
                    "filename": "东风天锦_D530改型驾驶室_整车电路图_彩绘版",
                    "ggzj_data_type": 3,
                }
            ],
            body_keyword="油门踏板",
            candidate_query="东风天锦整车电路图",
            max_candidate_docs=20,
            trace_callback=collect_trace,
        )
    )

    assert enhanced == [
        {
            "file_id": "a",
            "filename": "东风天锦_D530改型驾驶室_整车电路图_彩绘版",
            "ggzj_data_type": 3,
        }
    ]
    event_types = [event["event_type"] for event in trace_events]
    assert "circuit_candidate_hit_skipped" in event_types
    completed = next(event for event in trace_events if event["event_type"] == "circuit_body_search_completed")
    assert completed["payload"]["inserted_candidate_hit_count"] == 0
    assert completed["payload"]["attached_candidate_hit_count"] == 0


def test_preview_token_codec_round_trips_and_rejects_tamper():
    codec = CircuitBodyPreviewTokenCodec(secret="secret")
    token = codec.encode(
        {
            "pdf_id": "pdf_1",
            "latest_result_path": "/tmp/result.json",
            "source_pdf_url": "https://example.com/a.pdf",
            "page_index": 2,
            "highlight_boxes_px": [[1, 2, 3, 4]],
        },
        ttl_seconds=60,
    )

    payload = codec.decode(token)

    assert payload.pdf_id == "pdf_1"
    assert payload.keyword == ""
    assert payload.page_index == 2
    assert payload.highlight_boxes_px == [[1.0, 2.0, 3.0, 4.0]]
    with pytest.raises(CircuitBodyPreviewTokenError):
        codec.decode(f"{token}x")


def test_preview_renderer_crops_from_parser_page_image(tmp_path):
    page_image = tmp_path / "page.png"
    Image.new("RGB", (400, 300), color="white").save(page_image)
    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 0,
                        "page_metadata": {
                            "rendered_width_px": 400,
                            "rendered_height_px": 300,
                            "dpi": 600,
                            "image_path": str(page_image),
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    renderer = CircuitBodyPreviewRenderer(
        config_provider=SimpleNamespace(
            load=lambda: SimpleNamespace(preview_result_base_dir="", preview_pdf_timeout=1)
        )
    )

    content, media_type = renderer.render(
        CircuitBodyPreviewTokenPayload(
            pdf_id="pdf_1",
            latest_result_path=str(result_json),
            source_pdf_url="",
            page_index=0,
            highlight_boxes_px=[[100, 80, 160, 120]],
        )
    )

    preview = Image.open(BytesIO(content))
    assert media_type == "image/png"
    assert preview.width <= 400
    assert preview.height <= 300


def test_preview_renderer_returns_viewer_metadata_and_full_page_image(tmp_path):
    page_image = tmp_path / "page.png"
    Image.new("RGB", (400, 300), color="white").save(page_image)
    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 0,
                        "page_metadata": {
                            "rendered_width_px": 400,
                            "rendered_height_px": 300,
                            "dpi": 600,
                            "image_path": str(page_image),
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    renderer = CircuitBodyPreviewRenderer(
        config_provider=SimpleNamespace(
            load=lambda: SimpleNamespace(preview_result_base_dir="", preview_pdf_timeout=1)
        )
    )
    payload = CircuitBodyPreviewTokenPayload(
        pdf_id="pdf_1",
        filename="A电路图",
        keyword="油门踏板",
        hit_id="hit_1",
        latest_result_path=str(result_json),
        source_pdf_url="",
        page_index=0,
        highlight_boxes_px=[[100, 80, 160, 120]],
    )

    metadata = renderer.metadata(payload)
    content, media_type = renderer.render_page(payload, page_index=0)

    page = Image.open(BytesIO(content))
    assert metadata["filename"] == "A电路图"
    assert metadata["keyword"] == "油门踏板"
    assert metadata["initial_hit_id"] == "hit_1"
    assert metadata["total_pages"] == 1
    assert metadata["pages"][0]["width_px"] == 400
    assert media_type == "image/png"
    assert page.width == 400
    assert page.height == 300


def test_viewer_point_locator_normalizes_pixel_bbox_from_result_json(tmp_path):
    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 3,
                        "page_metadata": {
                            "rendered_width_px": 7016.6667,
                            "rendered_height_px": 4958.3333,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = CircuitBodyPreviewTokenPayload(
        pdf_id="pdf_1",
        filename="A电路图",
        keyword="ECU",
        hit_id="hit_1",
        latest_result_path=str(result_json),
        page_index=3,
        highlight_boxes_px=[],
    )

    points = CircuitBodyViewerPointLocator().points_for_bbox(
        payload=payload,
        page_index=3,
        bbox=[965.7778, 1292.0, 1039.4444, 1355.0],
    )

    parts = [float(part) for part in points.split(",")]
    assert parts == pytest.approx([0.13764, 0.26057, 0.14813, 0.27328], abs=0.00002)


def test_circuit_body_preview_endpoint_returns_renderer_image():
    app = create_app()

    class FakeCodec:
        def decode(self, token):
            assert token == "ok"
            return CircuitBodyPreviewTokenPayload(page_index=0, highlight_boxes_px=[[1, 2, 3, 4]])

    class FakeRenderer:
        def render(self, payload):
            assert payload.page_index == 0
            return b"image-bytes", "image/png"

    with TestClient(app) as client:
        app.state.runtime_deps = SimpleNamespace(
            circuit_body_preview_token_codec=FakeCodec(),
            circuit_body_preview_renderer=FakeRenderer(),
        )
        response = client.get("/chat/api/circuit-body-search/preview/ok")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"image-bytes"


def test_circuit_body_viewer_endpoints_return_metadata_image_and_search_results():
    app = create_app()

    class FakeCodec:
        def decode(self, token):
            assert token == "ok"
            return CircuitBodyPreviewTokenPayload(
                pdf_id="pdf_1",
                filename="A电路图",
                keyword="油门踏板",
                hit_id="hit_1",
                page_index=2,
                highlight_boxes_px=[[10, 20, 30, 40]],
            )

    class FakeRenderer:
        def metadata(self, payload):
            assert payload.pdf_id == "pdf_1"
            return {
                "pdf_id": payload.pdf_id,
                "filename": payload.filename,
                "keyword": payload.keyword,
                "initial_hit_id": payload.hit_id,
                "initial_page_index": payload.page_index,
                "total_pages": 3,
                "pages": [{"page_index": 2, "page_number": 3, "width_px": 400, "height_px": 300}],
            }

        def render_page(self, payload, *, page_index):
            assert payload.pdf_id == "pdf_1"
            assert page_index == 2
            return b"page-image", "image/png"

    class FakeSearchClient:
        async def search(self, *, pdf_id, keyword):
            assert pdf_id == "pdf_1"
            assert keyword == "油门踏板"
            return {
                "data": {
                    "total_matches": 1,
                    "results": [
                        {
                            "match_id": "hit_1",
                            "page_index": 2,
                            "element_index": 4,
                            "reading_order": 8,
                            "matched_text": "油门踏板",
                            "context": "电子油门踏板信号",
                            "bounding_box": {"x_min": 10, "y_min": 20, "x_max": 30, "y_max": 40},
                        }
                    ],
                }
            }

    class FakePointLocator:
        def points_for_bbox(self, *, payload, page_index, bbox, raw_hit=None):
            assert payload.pdf_id == "pdf_1"
            assert page_index == 2
            assert bbox == [10.0, 20.0, 30.0, 40.0]
            assert raw_hit["match_id"] == "hit_1"
            return "0.025,0.066667,0.075,0.133333"

    with TestClient(app) as client:
        app.state.runtime_deps = SimpleNamespace(
            circuit_body_preview_token_codec=FakeCodec(),
            circuit_body_preview_renderer=FakeRenderer(),
            circuit_body_search_client=FakeSearchClient(),
            circuit_body_viewer_point_locator=FakePointLocator(),
        )
        metadata_response = client.get("/chat/api/circuit-body-search/viewer/ok/metadata")
        image_response = client.get("/chat/api/circuit-body-search/viewer/ok/page/2/image")
        search_response = client.post(
            "/chat/api/circuit-body-search/viewer/ok/search",
            json={"keyword": "油门踏板"},
        )

    assert metadata_response.status_code == 200
    assert metadata_response.json()["initial_hit_id"] == "hit_1"
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content == b"page-image"
    assert search_response.status_code == 200
    payload = search_response.json()
    assert payload["total_matches"] == 1
    assert payload["results"][0]["hit_id"] == "hit_1"
    assert payload["results"][0]["bbox_px"] == [10.0, 20.0, 30.0, 40.0]
    assert payload["results"][0]["points"] == "0.025,0.066667,0.075,0.133333"
