from __future__ import annotations

from collections import Counter
from typing import Any


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def summarize_failures(case_results) -> dict[str, Any]:
    blocking_counter: Counter[str] = Counter()
    warning_counter: Counter[str] = Counter()
    capability_gap_counter: Counter[str] = Counter()
    stop_reason_counter: Counter[str] = Counter()
    final_status_counter: Counter[str] = Counter()
    failure_reason_counter: Counter[str] = Counter()
    blocking_cases: list[dict[str, Any]] = []
    capability_gap_cases: list[dict[str, Any]] = []
    capability_gap_attempt_count = 0
    ambiguous_attempt_count = 0
    corrected_attempt_count = 0

    for item in case_results:
        blocking = list(item.validation.blocking_failures or [])
        warnings = list(item.validation.warnings or [])
        capability_gaps = list(item.workflow.capability_gaps or [])
        stop_reason = str(item.workflow.stop_reason or "").strip()
        final_status = str(item.response.final_status or "").strip()
        failure_reason = str(item.analysis.failure_reason or "").strip()
        for code in blocking:
            blocking_counter[str(code)] += 1
        for code in warnings:
            warning_counter[str(code)] += 1
        for gap in capability_gaps:
            capability_gap_counter[str(gap)] += 1
        if stop_reason:
            stop_reason_counter[stop_reason] += 1
        if final_status:
            final_status_counter[final_status] += 1
        if failure_reason:
            failure_reason_counter[failure_reason] += 1
        if item.analysis.ambiguous_turn_count > 0:
            ambiguous_attempt_count += 1
        if item.analysis.correction_count > 0:
            corrected_attempt_count += 1
        if blocking:
            blocking_cases.append(
                {
                    "case_id": item.case_id,
                    "attempt_index": item.attempt_index,
                    "suite_id": item.suite_id,
                    "split": item.split,
                    "layer": item.layer,
                    "interaction_mode": item.task_metadata.interaction_mode,
                    "page_goal_mode": item.task_metadata.page_goal_mode,
                    "final_status": item.response.final_status,
                    "stop_reason": item.workflow.stop_reason,
                    "failure_reason": item.analysis.failure_reason,
                    "blocking_failures": blocking,
                    "capability_gaps": capability_gaps,
                }
            )
        if capability_gaps:
            capability_gap_attempt_count += 1
            capability_gap_cases.append(
                {
                    "case_id": item.case_id,
                    "attempt_index": item.attempt_index,
                    "suite_id": item.suite_id,
                    "split": item.split,
                    "layer": item.layer,
                    "interaction_mode": item.task_metadata.interaction_mode,
                    "page_goal_mode": item.task_metadata.page_goal_mode,
                    "final_status": item.response.final_status,
                    "stop_reason": item.workflow.stop_reason,
                    "failure_reason": item.analysis.failure_reason,
                    "capability_gaps": capability_gaps,
                    "blocking_failures": blocking,
                }
            )

    return {
        "count_basis": "attempt",
        "blocking_failure_counts": _sorted_counts(blocking_counter),
        "warning_counts": _sorted_counts(warning_counter),
        "capability_gap_counts": _sorted_counts(capability_gap_counter),
        "capability_gap_attempt_count": capability_gap_attempt_count,
        "failure_reason_counts": _sorted_counts(failure_reason_counter),
        "ambiguous_attempt_count": ambiguous_attempt_count,
        "corrected_attempt_count": corrected_attempt_count,
        "stop_reason_counts": _sorted_counts(stop_reason_counter),
        "final_status_counts": _sorted_counts(final_status_counter),
        "blocking_cases": blocking_cases,
        "capability_gap_cases": capability_gap_cases,
    }
