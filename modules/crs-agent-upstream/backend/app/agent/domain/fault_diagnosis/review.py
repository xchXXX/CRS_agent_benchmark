"""Deterministic review helpers for fault-diagnosis execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.context.models import CaseContext, CaseContextArtifactType
from app.agent.models.tool_result import ClarifyCandidate, ClarifyCandidateOption, ToolResultEnvelope, ToolResultStatus


@dataclass(frozen=True)
class FaultDiagnosisToolReviewResult:
    blocked: bool
    envelope: dict[str, Any] | None = None
    reason: str | None = None


def review_fault_diagnosis_execution(
    *,
    case_context: CaseContext | None,
    runtime_tool_history: list[dict[str, Any]] | None,
    fault_code: str,
    ecu_model: str,
) -> FaultDiagnosisToolReviewResult:
    normalized_fault_code = str(fault_code or "").strip().upper()
    normalized_ecu_model = str(ecu_model or "").strip().upper()
    selected_ecu_model = str(case_context.slots.ecu_model or "").strip().upper() if case_context is not None else ""

    latest_lookup = _latest_lookup_state(
        case_context=case_context,
        runtime_tool_history=runtime_tool_history,
        fault_code=normalized_fault_code,
    )
    if latest_lookup is not None:
        count = int(latest_lookup.get("count") or 0)
        candidates = [str(item).strip().upper() for item in (latest_lookup.get("candidates") or []) if item]
        if count <= 0:
            return FaultDiagnosisToolReviewResult(
                blocked=True,
                envelope=_build_failed_lookup_envelope(normalized_fault_code),
                reason="no_ecu_candidates",
            )
        if count > 1:
            if selected_ecu_model and normalized_ecu_model == selected_ecu_model:
                pass
            else:
                return FaultDiagnosisToolReviewResult(
                    blocked=True,
                    envelope=_build_need_clarify_envelope(
                        fault_code=normalized_fault_code,
                        candidates=candidates,
                    ),
                    reason="ecu_not_confirmed",
                )

    latest_diagnosis = _latest_diagnosis_state(
        case_context=case_context,
        runtime_tool_history=runtime_tool_history,
        fault_code=normalized_fault_code,
        ecu_model=normalized_ecu_model,
    )
    if latest_diagnosis is not None:
        status = str(latest_diagnosis.get("status") or "").strip().lower()
        if status in {"failed", "ok"}:
            reason = "repeat_failed_diagnosis" if status == "failed" else "repeat_existing_diagnosis"
            return FaultDiagnosisToolReviewResult(
                blocked=True,
                envelope=_build_diagnosis_envelope(latest_diagnosis),
                reason=reason,
            )

    return FaultDiagnosisToolReviewResult(blocked=False)


def _latest_lookup_state(
    *,
    case_context: CaseContext | None,
    runtime_tool_history: list[dict[str, Any]] | None,
    fault_code: str,
) -> dict[str, Any] | None:
    for item in reversed(runtime_tool_history or []):
        if item.get("tool_name") != "lookup_ecu_candidates":
            continue
        result = item.get("result") or {}
        data = result.get("data") or {}
        if str(data.get("fault_code") or "").strip().upper() == fault_code:
            return {
                "status": result.get("status"),
                "fault_code": data.get("fault_code"),
                "count": data.get("count"),
                "candidates": data.get("candidates") or [],
                "message": data.get("message"),
            }

    if case_context is None:
        return None
    for artifact in reversed(case_context.artifacts):
        if artifact.type != CaseContextArtifactType.DIAGNOSIS_RESULT:
            continue
        if artifact.structured_data.get("tool_name") != "lookup_ecu_candidates":
            continue
        if str(artifact.structured_data.get("fault_code") or "").strip().upper() != fault_code:
            continue
        return artifact.structured_data
    return None


def _latest_diagnosis_state(
    *,
    case_context: CaseContext | None,
    runtime_tool_history: list[dict[str, Any]] | None,
    fault_code: str,
    ecu_model: str,
) -> dict[str, Any] | None:
    for item in reversed(runtime_tool_history or []):
        if item.get("tool_name") != "dtc_diagnosis":
            continue
        result = item.get("result") or {}
        data = result.get("data") or {}
        if (
            str(data.get("fault_code") or "").strip().upper() == fault_code
            and str(data.get("ecu_model") or "").strip().upper() == ecu_model
        ):
            return {
                "status": result.get("status"),
                "state": data.get("state"),
                "fault_code": data.get("fault_code"),
                "ecu_model": data.get("ecu_model"),
                "report_url": data.get("report_url"),
                "task_id": data.get("task_id"),
                "subscribe_url": data.get("subscribe_url"),
                "report_id": data.get("report_id"),
                "message": data.get("message"),
                "error": data.get("error"),
            }

    if case_context is None:
        return None
    for artifact in reversed(case_context.artifacts):
        if artifact.type != CaseContextArtifactType.DIAGNOSIS_RESULT:
            continue
        if artifact.structured_data.get("tool_name") != "dtc_diagnosis":
            continue
        if (
            str(artifact.structured_data.get("fault_code") or "").strip().upper() == fault_code
            and str(artifact.structured_data.get("ecu_model") or "").strip().upper() == ecu_model
        ):
            return {
                "status": artifact.structured_data.get("status"),
                "state": artifact.structured_data.get("state"),
                "fault_code": artifact.structured_data.get("fault_code"),
                "ecu_model": artifact.structured_data.get("ecu_model"),
                "report_url": artifact.structured_data.get("report_url"),
                "task_id": artifact.structured_data.get("task_id"),
                "subscribe_url": artifact.structured_data.get("subscribe_url"),
                "report_id": artifact.structured_data.get("report_id"),
                "message": artifact.summary,
                "error": artifact.structured_data.get("error"),
            }
    return None


def _build_need_clarify_envelope(
    *,
    fault_code: str,
    candidates: list[str],
) -> dict[str, Any]:
    return ToolResultEnvelope(
        status=ToolResultStatus.NEED_CLARIFY,
        data={
            "success": True,
            "fault_code": fault_code,
            "candidates": candidates,
            "count": len(candidates),
            "message": f"故障码 {fault_code} 关联多个 ECU，请先选择。",
            "auto_selected_ecu": None,
        },
        clarify=ClarifyCandidate(
            source="fault_diagnosis_review",
            question=f"识别到故障码 {fault_code}，请选择对应 ECU：",
            results_count=len(candidates),
            context={
                "fault_code": fault_code,
                "message": f"故障码 {fault_code} 关联多个 ECU，请先选择。",
                "blocked_reason": "ecu_not_confirmed",
            },
            options=[
                ClarifyCandidateOption(
                    key=ecu,
                    label=ecu,
                    selection_payload={
                        "filters": {"fault_code": fault_code, "ecu_model": ecu},
                        "file_ids": [],
                    },
                )
                for ecu in candidates
            ],
        ),
        metadata={"review_blocked_reason": "ecu_not_confirmed"},
    ).model_dump(mode="json")


def _build_failed_lookup_envelope(fault_code: str) -> dict[str, Any]:
    return ToolResultEnvelope(
        status=ToolResultStatus.FAILED,
        data={
            "success": False,
            "fault_code": fault_code,
            "message": f"系统中暂无故障码 {fault_code} 的关联 ECU 信息，请先确认报码是否正确。",
            "error": {
                "code": "NO_ECU_CANDIDATES",
                "message": f"系统中暂无故障码 {fault_code} 的关联 ECU 信息，请先确认报码是否正确。",
            },
        },
        metadata={"review_blocked_reason": "no_ecu_candidates"},
    ).model_dump(mode="json")


def _build_diagnosis_envelope(state: dict[str, Any]) -> dict[str, Any]:
    status = str(state.get("status") or "").strip().lower()
    return ToolResultEnvelope(
        status=ToolResultStatus(status if status in {"ok", "failed"} else "failed"),
        data={
            "success": status == "ok",
            "state": state.get("state") or ("ready" if status == "ok" else "failed"),
            "fault_code": state.get("fault_code"),
            "ecu_model": state.get("ecu_model"),
            "report_url": state.get("report_url"),
            "task_id": state.get("task_id"),
            "subscribe_url": state.get("subscribe_url"),
            "report_id": state.get("report_id"),
            "message": state.get("message") or "诊断结果已存在。",
            "error": state.get("error"),
        },
        metadata={
            "review_blocked_reason": "repeat_failed_diagnosis" if status == "failed" else "repeat_existing_diagnosis"
        },
    ).model_dump(mode="json")
