"""Structured image evidence models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ImageEvidenceScene(str, Enum):
    VEHICLE_IDENTITY = "vehicle_identity"
    DIAGNOSTIC_SCREEN = "diagnostic_screen"
    REPAIR_SCENE = "repair_scene"
    DOCUMENT_HINT = "document_hint"
    UNKNOWN = "unknown"


class ImageEvidenceImageInput(BaseModel):
    filename: str
    content: bytes
    content_type: str = "image/jpeg"

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized.startswith("image/"):
            raise ValueError("只支持图片文件")
        return normalized


class ImageEvidenceRequest(BaseModel):
    images: list[ImageEvidenceImageInput] = Field(default_factory=list)
    user_prompt: str | None = None


class ImageEvidenceVehicleInfo(BaseModel):
    brand: str | None = None
    series: str | None = None
    model: str | None = None
    platform: str | None = None
    engine: str | None = None
    emission: str | None = None
    vin: str | None = None
    license_plate: str | None = None


class ImageEvidenceDiagnosticInfo(BaseModel):
    fault_codes: list[str] = Field(default_factory=list)
    descriptions: list[str] = Field(default_factory=list)
    ecu_model: str | None = None
    status: str | None = None


class ImageEvidenceAnalysis(BaseModel):
    image_evidence_id: str
    scene: ImageEvidenceScene = ImageEvidenceScene.UNKNOWN
    summary: str = ""
    vehicle: ImageEvidenceVehicleInfo = Field(default_factory=ImageEvidenceVehicleInfo)
    diagnosis: ImageEvidenceDiagnosticInfo = Field(default_factory=ImageEvidenceDiagnosticInfo)
    visible_text: list[str] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_user_confirm: bool = True
    raw: dict[str, Any] = Field(default_factory=dict)

    def to_context_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ImageEvidenceResponse(BaseModel):
    success: bool
    evidence: ImageEvidenceAnalysis | None = None
    error: dict[str, Any] | None = None
