"""Legacy diagnosis services adapted into the new project."""

from app.legacy.services.diagnosis.diagnosis_client import (
    BatchEcusResult,
    BatchReportItem,
    BatchReportsResult,
    DiagnosisResult,
    DiagnosisServiceClient,
    EcusByFaultCodeResult,
    EcuSummaryItem,
    ImageRecognitionResult,
    RecognizedFaultCode,
    get_diagnosis_client,
)
from app.legacy.services.diagnosis.ecu_service import ECUService, get_ecu_service
from app.legacy.services.diagnosis.fault_code_parser import (
    FaultCodeParser,
    ParsedFaultCode,
    get_fault_code_parser,
)

__all__ = [
    "BatchEcusResult",
    "BatchReportItem",
    "BatchReportsResult",
    "DiagnosisResult",
    "DiagnosisServiceClient",
    "ECUService",
    "EcusByFaultCodeResult",
    "EcuSummaryItem",
    "FaultCodeParser",
    "ImageRecognitionResult",
    "ParsedFaultCode",
    "RecognizedFaultCode",
    "get_diagnosis_client",
    "get_ecu_service",
    "get_fault_code_parser",
]
