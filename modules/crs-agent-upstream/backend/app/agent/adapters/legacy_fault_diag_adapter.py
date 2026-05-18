"""Adapter layer for migrated legacy fault diagnosis capabilities."""

from app.agent.domain.fault_diagnosis.service import FaultDiagnosisService
from app.agent.models.tool_result import (
    ClarifyCandidate,
    ClarifyCandidateOption,
    ToolResultEnvelope,
    ToolResultStatus,
)
from app.agent.runtime.deps import AgentRuntimeDeps
from app.core.config import settings
from app.legacy.services.diagnosis import (
    get_diagnosis_client,
    get_fault_code_parser,
)


class LegacyFaultDiagAdapter:
    """Bridge between Agent Loop tools and migrated diagnosis services."""

    def __init__(self, deps: AgentRuntimeDeps):
        self._deps = deps
        self._service = FaultDiagnosisService(
            diagnosis_client=deps.diagnosis_client or get_diagnosis_client(),
            fault_code_parser=deps.fault_code_parser or get_fault_code_parser(),
            diagnosis_enabled_provider=self._is_diagnosis_enabled,
        )

    async def lookup_ecu_candidates(self, fault_code: str) -> dict:
        result = await self._service.lookup_ecu_candidates(fault_code)
        if not result.success:
            return ToolResultEnvelope(
                status=ToolResultStatus.FAILED,
                data=result.model_dump(mode="json"),
            ).model_dump(mode="json")

        if result.count > 1:
            return ToolResultEnvelope(
                status=ToolResultStatus.NEED_CLARIFY,
                data=result.model_dump(mode="json"),
                clarify=ClarifyCandidate(
                    source="rule",
                    question=f"识别到故障码 {result.fault_code}，请选择对应 ECU：",
                    results_count=result.count,
                    context={"fault_code": result.fault_code, "message": result.message},
                    options=[
                        ClarifyCandidateOption(
                            key=ecu_model,
                            label=ecu_model,
                            description=None,
                            selection_payload={
                                "filters": {"fault_code": result.fault_code, "ecu_model": ecu_model},
                                "file_ids": [],
                            },
                        )
                        for ecu_model in result.candidates
                    ],
                ),
            ).model_dump(mode="json")

        return ToolResultEnvelope(
            status=ToolResultStatus.OK,
            data=result.model_dump(mode="json"),
        ).model_dump(mode="json")

    async def diagnose(self, fault_code: str, ecu_model: str) -> dict:
        result = await self._service.diagnose(fault_code=fault_code, ecu_model=ecu_model)
        status = ToolResultStatus.OK if result.success else ToolResultStatus.FAILED
        return ToolResultEnvelope(
            status=status,
            data=result.model_dump(mode="json"),
        ).model_dump(mode="json")

    async def recognize_image(self, image_content: bytes, filename: str) -> dict:
        return (await self._service.recognize_image(image_content, filename)).model_dump(mode="json")

    async def get_batch_ecus(self, fault_codes: list[str]) -> dict:
        return (await self._service.get_batch_ecus(fault_codes)).model_dump(mode="json")

    async def get_batch_reports(self, fault_codes: list[str], ecu_model: str, return_url: str | None = None) -> dict:
        return (await self._service.get_batch_reports(fault_codes, ecu_model, return_url)).model_dump(mode="json")

    def _is_diagnosis_enabled(self) -> bool:
        if self._deps.config_service is not None:
            return bool(self._deps.config_service.get("diagnosis_service_enabled", settings.diagnosis_service_enabled))
        return settings.diagnosis_service_enabled
