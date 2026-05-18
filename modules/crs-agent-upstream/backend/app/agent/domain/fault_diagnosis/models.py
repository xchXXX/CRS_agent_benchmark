"""Typed models for fault diagnosis domain."""

from typing import Any

from pydantic import BaseModel, Field


class FaultDiagnosisError(BaseModel):
    code: str
    message: str


class FaultCodeParseResult(BaseModel):
    original: str
    normalized: str
    code_type: str
    is_valid: bool = True


class EcuCandidateLookupResult(BaseModel):
    success: bool
    fault_code: str
    candidates: list[str] = Field(default_factory=list)
    count: int = 0
    message: str = ""
    auto_selected_ecu: str | None = None
    error: FaultDiagnosisError | None = None


class DiagnosisExecutionResult(BaseModel):
    success: bool
    state: str
    fault_code: str
    ecu_model: str
    report_url: str | None = None
    task_id: str | None = None
    subscribe_url: str | None = None
    report_id: int | None = None
    message: str
    error: FaultDiagnosisError | None = None


class ImageRecognitionPayload(BaseModel):
    success: bool
    fault_codes: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    error: str | None = None


class BatchEcusPayload(BaseModel):
    success: bool
    ecu_summary: list[dict[str, Any]] = Field(default_factory=list)
    code_details: dict[str, list[str]] = Field(default_factory=dict)
    error: str | None = None


class BatchReportsPayload(BaseModel):
    success: bool
    ecu_model: str
    reports: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
