from __future__ import annotations

from typing import Any


def validate_task_shape(task) -> list[str]:
    errors: list[str] = []
    if not getattr(task, "user_id", ""):
        errors.append("task.user_id missing")
    if not getattr(task, "instruction", ""):
        errors.append("task.instruction missing")
    if getattr(task, "interaction_mode", "") not in {"single_turn", "multi_turn"}:
        errors.append("task.interaction_mode invalid")
    if not isinstance(getattr(task, "max_turns", None), int) or task.max_turns < 1:
        errors.append("task.max_turns invalid")
    if not isinstance(getattr(task, "stop_tokens", None), list) or len(task.stop_tokens) == 0:
        errors.append("task.stop_tokens missing")
    if not getattr(task, "initial_user_message", None):
        errors.append("task.initial_user_message missing")
    return errors


def validate_case_shape(result) -> list[str]:
    errors: list[str] = []
    if not result.case_id:
        errors.append("missing case_id")
    if not result.split:
        errors.append("missing split")
    if not result.layer:
        errors.append("missing layer")
    if not isinstance(result.input, dict):
        errors.append("input must be object")
    if not isinstance(result.validation.blocking_failures, list):
        errors.append("blocking_failures must be array")
    if not isinstance(result.prediction.top_k_documents, list):
        errors.append("top_k_documents must be array")
    if not isinstance(result.workflow.messages, list):
        errors.append("workflow.messages must be array")
    if not isinstance(result.workflow.turns, list):
        errors.append("workflow.turns must be array")
    if not isinstance(result.analysis.decision_trace, list):
        errors.append("analysis.decision_trace must be array")
    if not isinstance(result.analysis.turn_count, int):
        errors.append("analysis.turn_count must be integer")
    for idx, doc in enumerate(result.prediction.top_k_documents):
        if not doc.doc_title:
            errors.append(f"prediction.top_k_documents[{idx}].doc_title missing")
        if not doc.doc_path:
            errors.append(f"prediction.top_k_documents[{idx}].doc_path missing")
    return errors


def judge_contract(task, result) -> dict[str, Any]:
    blocking_failures: list[str] = []
    warnings: list[str] = []
    task_shape_errors = validate_task_shape(task)
    if task_shape_errors:
        blocking_failures.append("SCHEMA_INVALID")
        warnings.extend(task_shape_errors)
    shape_errors = validate_case_shape(result)
    if shape_errors:
        blocking_failures.append("SCHEMA_INVALID")
        warnings.extend(shape_errors)

    has_runtime_failure = False
    if result.response.response_type == "error":
        blocking_failures.append("HTTP_OR_RUNTIME_ERROR")
        has_runtime_failure = True

    http_status = result.execution.http_status
    if isinstance(http_status, int) and http_status >= 400:
        blocking_failures.append("HTTP_OR_RUNTIME_ERROR")
        has_runtime_failure = True

    has_preprocess_failure = False
    if task.preprocess_strategy != "none" and not result.workflow.used_image_context:
        blocking_failures.append("OCR_CONTEXT_MISSING")
        has_preprocess_failure = True

    has_capability_gap = bool(result.workflow.capability_gaps)
    if has_capability_gap:
        blocking_failures.append("CAPABILITY_GAP_PRESENT")

    if task.expected_response_type == "documents":
        if (
            not has_runtime_failure
            and not has_preprocess_failure
            and not has_capability_gap
            and result.response.response_type != "documents"
        ):
            blocking_failures.append("EXPECTED_DOCUMENTS_RESPONSE")

    track_supports_turn_trace = getattr(task, "benchmark_track", "") != "search_api"
    required_ask_user_rounds = max(0, int(getattr(task, "required_ask_user_rounds", 0) or 0))
    actual_ask_user_rounds = int(getattr(result.workflow, "ask_user_rounds", 0) or 0)
    if (
        track_supports_turn_trace
        and required_ask_user_rounds > 0
        and not has_runtime_failure
        and not has_preprocess_failure
        and not has_capability_gap
        and actual_ask_user_rounds < required_ask_user_rounds
    ):
        blocking_failures.append("ASK_USER_ROUNDS_INSUFFICIENT")
        warnings.append(
            f"required_ask_user_rounds={required_ask_user_rounds}, actual_ask_user_rounds={actual_ask_user_rounds}"
        )
    if (
        track_supports_turn_trace
        and task.interaction_mode == "multi_turn"
        and not result.workflow.messages
        and not result.workflow.turns
    ):
        warnings.append("MULTI_TURN_TRACE_MISSING")
    if (
        track_supports_turn_trace
        and
        task.interaction_mode == "multi_turn"
        and result.workflow.conversation_turn_count <= 1
        and len(result.workflow.turns) <= 1
    ):
        warnings.append("MULTI_TURN_NOT_EXECUTED")
    if task.outputs and not result.workflow.final_agent_response and not result.prediction.top_k_documents:
        warnings.append("FINAL_OUTPUT_TEXT_MISSING")

    return {
        "schema_pass": not shape_errors and not task_shape_errors,
        "blocking_failures": sorted(set(blocking_failures)),
        "warnings": sorted(set(warnings)),
    }
