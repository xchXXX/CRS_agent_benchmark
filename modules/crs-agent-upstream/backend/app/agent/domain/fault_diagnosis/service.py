"""Facade service for fault diagnosis domain."""

from typing import Any

from app.agent.domain.fault_diagnosis.models import (
    BatchEcusPayload,
    BatchReportsPayload,
    DiagnosisExecutionResult,
    EcuCandidateLookupResult,
    FaultCodeParseResult,
    FaultDiagnosisError,
    ImageRecognitionPayload,
)


class FaultDiagnosisService:
    """Stable domain entrypoint for migrated fault diagnosis capability."""

    def __init__(
        self,
        *,
        diagnosis_client: Any,
        fault_code_parser: Any,
        diagnosis_enabled_provider: Any,
    ):
        self._diagnosis_client = diagnosis_client
        self._fault_code_parser = fault_code_parser
        self._diagnosis_enabled_provider = diagnosis_enabled_provider

    def parse_fault_code(self, text: str) -> FaultCodeParseResult | None:
        parsed = self._fault_code_parser.parse_first(text) if self._fault_code_parser is not None else None
        if parsed is None:
            return None
        return FaultCodeParseResult(
            original=parsed.original,
            normalized=parsed.normalized,
            code_type=parsed.code_type,
            is_valid=parsed.is_valid,
        )

    async def lookup_ecu_candidates(self, fault_code: str) -> EcuCandidateLookupResult:
        if not self._diagnosis_enabled_provider():
            return EcuCandidateLookupResult(
                success=False,
                fault_code=fault_code,
                message="诊断服务未启用",
                error=FaultDiagnosisError(code="DIAGNOSIS_DISABLED", message="诊断服务未启用"),
            )

        parsed = self.parse_fault_code(fault_code)
        if parsed is None:
            return EcuCandidateLookupResult(
                success=False,
                fault_code=fault_code,
                message="未识别到有效故障码",
                error=FaultDiagnosisError(code="INVALID_FAULT_CODE", message="未识别到有效故障码"),
            )

        result = await self._diagnosis_client.get_ecus_by_fault_code(parsed.normalized)
        if not result.success:
            error = result.error or {"code": "LOOKUP_FAILED", "message": "查询失败"}
            return EcuCandidateLookupResult(
                success=False,
                fault_code=parsed.normalized,
                message=error.get("message", "查询失败"),
                error=FaultDiagnosisError(
                    code=str(error.get("code", "LOOKUP_FAILED")),
                    message=str(error.get("message", "查询失败")),
                ),
            )

        candidates = [candidate.strip().upper() for candidate in result.ecu_models if candidate]
        if result.count == 0:
            message = f"系统中暂无故障码 {parsed.normalized} 的关联 ECU 信息。"
        elif result.count == 1:
            message = f"故障码 {parsed.normalized} 命中唯一 ECU：{candidates[0]}"
        else:
            message = f"故障码 {parsed.normalized} 关联多个 ECU，请选择。"

        return EcuCandidateLookupResult(
            success=True,
            fault_code=parsed.normalized,
            candidates=candidates,
            count=len(candidates),
            message=message,
            auto_selected_ecu=candidates[0] if len(candidates) == 1 else None,
        )

    async def diagnose(self, fault_code: str, ecu_model: str) -> DiagnosisExecutionResult:
        if not self._diagnosis_enabled_provider():
            return DiagnosisExecutionResult(
                success=False,
                state="failed",
                fault_code=fault_code,
                ecu_model=ecu_model,
                message="诊断服务未启用",
                error=FaultDiagnosisError(code="DIAGNOSIS_DISABLED", message="诊断服务未启用"),
            )

        parsed = self.parse_fault_code(fault_code)
        if parsed is None:
            return DiagnosisExecutionResult(
                success=False,
                state="failed",
                fault_code=fault_code,
                ecu_model=ecu_model,
                message="未识别到有效故障码",
                error=FaultDiagnosisError(code="INVALID_FAULT_CODE", message="未识别到有效故障码"),
            )

        normalized_ecu = ecu_model.strip().upper()
        result = await self._diagnosis_client.ensure_latest(
            ecu_model=normalized_ecu,
            fault_code=parsed.normalized,
            show_back=True,
        )

        if not result.success or result.state == "failed":
            error = result.error or {"code": "DIAGNOSIS_FAILED", "message": "诊断失败"}
            return DiagnosisExecutionResult(
                success=False,
                state="failed",
                fault_code=parsed.normalized,
                ecu_model=normalized_ecu,
                message=error.get("message", "诊断失败"),
                error=FaultDiagnosisError(
                    code=str(error.get("code", "DIAGNOSIS_FAILED")),
                    message=str(error.get("message", "诊断失败")),
                ),
            )

        return DiagnosisExecutionResult(
            success=True,
            state=result.state,
            fault_code=parsed.normalized,
            ecu_model=normalized_ecu,
            report_url=result.report_url,
            task_id=result.task_id,
            subscribe_url=result.subscribe_url,
            report_id=result.report_id,
            message=self._build_response_message(result.state, parsed.normalized, normalized_ecu),
        )

    async def recognize_image(self, image_content: bytes, filename: str) -> ImageRecognitionPayload:
        result = await self._diagnosis_client.recognize_image(image_content, filename)
        if not result.success:
            error_msg = result.error.get("message", "识别失败") if result.error else "识别失败"
            return ImageRecognitionPayload(success=False, fault_codes=[], count=0, error=error_msg)

        return ImageRecognitionPayload(
            success=True,
            fault_codes=[
                {
                    "raw": item.raw,
                    "normalized": item.normalized,
                    "type": item.code_type,
                    "description": item.description,
                    "status": item.status,
                }
                for item in result.fault_codes
            ],
            count=result.count,
        )

    async def get_batch_ecus(self, fault_codes: list[str]) -> BatchEcusPayload:
        result = await self._diagnosis_client.get_batch_ecus(fault_codes)
        if not result.success:
            error_msg = result.error.get("message", "查询失败") if result.error else "查询失败"
            return BatchEcusPayload(success=False, ecu_summary=[], code_details={}, error=error_msg)

        return BatchEcusPayload(
            success=True,
            ecu_summary=[
                {
                    "ecu_model": item.ecu_model,
                    "match_count": item.match_count,
                    "matched_codes": item.matched_codes,
                    "recommended": item.recommended,
                }
                for item in result.ecu_summary
            ],
            code_details=result.code_details,
        )

    async def get_batch_reports(
        self,
        fault_codes: list[str],
        ecu_model: str,
        return_url: str | None = None,
    ) -> BatchReportsPayload:
        result = await self._diagnosis_client.get_batch_reports(fault_codes, ecu_model, return_url)
        if not result.success:
            error_msg = result.error.get("message", "查询失败") if result.error else "查询失败"
            return BatchReportsPayload(success=False, ecu_model=ecu_model, reports=[], error=error_msg)

        return BatchReportsPayload(
            success=True,
            ecu_model=result.ecu_model,
            reports=[
                {
                    "fault_code": item.fault_code,
                    "state": item.state,
                    "report_url": item.report_url,
                    "task_id": item.task_id,
                    "subscribe_url": item.subscribe_url,
                    "report_id": item.report_id,
                }
                for item in result.reports
            ],
        )

    @staticmethod
    def _build_response_message(state: str, fault_code: str, ecu_model: str) -> str:
        if state == "ready":
            return f"故障码 {fault_code}（{ecu_model}）的诊断报告已生成，点击卡片查看详情。"
        if state == "generating":
            return f"正在生成故障码 {fault_code}（{ecu_model}）的诊断报告，完成后会通知您。"
        return f"故障码 {fault_code} 诊断状态：{state}"
