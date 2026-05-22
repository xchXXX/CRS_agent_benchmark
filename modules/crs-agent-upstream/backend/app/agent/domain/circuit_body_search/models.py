"""Models for circuit-diagram body search enrichment."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ParsedCircuitDocument(BaseModel):
    """A parsed PDF record resolved from the external parser database."""

    item_id: str
    name: str
    latest_pdf_id: str
    latest_result_path: str
    url_raw_sample: str = ""
    updated_at: datetime | None = None


class CircuitBodyBestHit(BaseModel):
    """Display-sized representative hit inside a parsed circuit diagram."""

    hit_id: str
    candidate_id: str = ""
    page_index: int
    page_number: int
    matched_text: str = ""
    snippet: str = ""
    context: str = ""
    nearby_ocr_text: str = ""
    highlight_boxes_px: list[list[float]] = Field(default_factory=list)
    source_hit_ids: list[str] = Field(default_factory=list)
    display_rank: int = 0
    score: float = 0.0
    confidence: Literal["high", "medium", "low"] = "medium"
    reason: str = ""
    viewer_token: str = ""
    preview_image_url: str = ""


class CircuitBodySearchSummary(BaseModel):
    """Reduced body-search result attached to one document-search result."""

    status: Literal["hit", "no_hit", "unsupported", "failed"]
    reason: str = ""
    match_source: str = "filename"
    pdf_id: str = ""
    keyword: str = ""
    source_pdf_url: str = ""
    viewer_token: str = ""
    viewer_url_type: str = ""
    raw_hit_count: int = 0
    page_hit_count: int = 0
    region_candidate_count: int = 0
    display_hit_count: int = 0
    best_hit: CircuitBodyBestHit | None = None
    top_hits: list[CircuitBodyBestHit] = Field(default_factory=list)
    more_hits_count: int = 0
    rerank_source: str = "rule"
