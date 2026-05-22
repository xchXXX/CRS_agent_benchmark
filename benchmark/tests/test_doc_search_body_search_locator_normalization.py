from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.envs.doc_search.env import extract_page_numbers, normalize_documents
from doc_search_bench.types import RegionPageBoxes


def test_extract_page_numbers_reads_body_search_best_hit_and_top_hits_page_numbers():
    item = {
        "filename": "电路图A",
        "body_search": {
            "status": "hit",
            "best_hit": {"page_number": 12},
            "top_hits": [
                {"page_number": 12},
                {"page_number": 13},
            ],
        },
    }

    assert extract_page_numbers(item) == [12, 13]


def test_normalize_documents_normalizes_highlight_boxes_px_with_viewer_metadata():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "电路图A",
                    "doc_path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "viewer_token": "viewer-token-001",
                        "metadata": {
                            "pages": [
                                {
                                    "page_number": 12,
                                    "width_px": 1000,
                                    "height_px": 500,
                                }
                            ]
                        },
                        "best_hit": {
                            "page_number": 12,
                            "highlight_boxes_px": [[100, 50, 300, 150]],
                        },
                        "top_hits": [
                            {
                                "page_number": 12,
                                "highlight_boxes_px": [[100, 50, 300, 150]],
                            }
                        ],
                    },
                }
            ]
        },
    }

    response_type, docs, predicted_pages, page_confidence, locator_summary = normalize_documents(
        "chat_completions",
        body,
    )

    assert response_type == "documents"
    assert page_confidence is None
    assert predicted_pages == [12]
    assert docs[0].coord_predicted_page_numbers == [12]
    assert docs[0].coord_predicted_boxes_px == [
        RegionPageBoxes(page_number=12, boxes=[(100.0, 50.0, 300.0, 150.0)])
    ]
    assert docs[0].coord_predicted_boxes_norm == [
        RegionPageBoxes(page_number=12, boxes=[(0.1, 0.1, 0.3, 0.3)])
    ]
    assert docs[0].coord_viewer_token == "viewer-token-001"
    assert docs[0].coord_metadata_present is True
    assert locator_summary["coord_predicted_page_numbers"] == [12]
    assert locator_summary["coord_predicted_boxes_px"] == [
        RegionPageBoxes(page_number=12, boxes=[(100.0, 50.0, 300.0, 150.0)])
    ]
    assert locator_summary["coord_predicted_boxes_norm"] == [
        RegionPageBoxes(page_number=12, boxes=[(0.1, 0.1, 0.3, 0.3)])
    ]
    assert locator_summary["coord_viewer_token"] == "viewer-token-001"
    assert locator_summary["coord_metadata_present"] is True


def test_normalize_documents_keeps_pixel_boxes_when_metadata_is_missing():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "电路图A",
                    "doc_path": "/docs/a.pdf",
                    "body_search": {
                        "status": "hit",
                        "viewer_token": "viewer-token-001",
                        "best_hit": {
                            "page_number": 12,
                            "highlight_boxes_px": [[100, 50, 300, 150]],
                        },
                    },
                }
            ]
        },
    }

    _, docs, predicted_pages, _, locator_summary = normalize_documents("chat_completions", body)

    assert predicted_pages == [12]
    assert docs[0].coord_predicted_page_numbers == [12]
    assert docs[0].coord_predicted_boxes_px == [
        RegionPageBoxes(page_number=12, boxes=[(100.0, 50.0, 300.0, 150.0)])
    ]
    assert docs[0].coord_predicted_boxes_norm == []
    assert docs[0].coord_viewer_token == "viewer-token-001"
    assert docs[0].coord_metadata_present is False
    assert locator_summary["coord_predicted_page_numbers"] == [12]
    assert locator_summary["coord_predicted_boxes_norm"] == []
    assert locator_summary["coord_viewer_token"] == "viewer-token-001"
    assert locator_summary["coord_metadata_present"] is False


def test_normalize_documents_ignores_missing_body_search_without_injecting_pages():
    body = {
        "type": "documents",
        "content": {
            "results": [
                {
                    "filename": "电路图A",
                    "doc_path": "/docs/a.pdf",
                }
            ]
        },
    }

    response_type, docs, predicted_pages, page_confidence, locator_summary = normalize_documents(
        "chat_completions",
        body,
    )

    assert response_type == "documents"
    assert len(docs) == 1
    assert docs[0].page_numbers == []
    assert predicted_pages == []
    assert page_confidence is None
    assert locator_summary["locator_source"] is None
    assert locator_summary["locator_top_pages"] == []
    assert locator_summary["coord_predicted_page_numbers"] == []
    assert locator_summary["coord_predicted_boxes_px"] == []
    assert locator_summary["coord_predicted_boxes_norm"] == []
    assert locator_summary["coord_viewer_token"] is None
    assert locator_summary["coord_metadata_present"] is None


def test_extract_page_numbers_keeps_legacy_page_fields_compatible():
    item = {
        "page": 11,
        "page_number": 7,
        "page_numbers": [7, 8],
        "pages": [10],
    }

    pages = extract_page_numbers(item)

    assert pages == [11, 7, 8, 10]
