from __future__ import annotations

from collections import Counter
from typing import Any


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _target_doc_count(item) -> int:
    explicit_count = _coerce_int(getattr(item.task_metadata, "target_doc_count", None))
    if explicit_count is not None:
        return max(explicit_count, 0)
    accepted_titles = getattr(item.task_metadata, "accepted_titles", None)
    if isinstance(accepted_titles, list):
        return len([title for title in accepted_titles if str(title or "").strip()])
    return 0


def _target_match_mode(item) -> str | None:
    explicit_mode = str(getattr(item.task_metadata, "target_match_mode", "") or "").strip()
    if explicit_mode:
        return explicit_mode
    target_doc_count = _target_doc_count(item)
    if target_doc_count > 1:
        return "any_of"
    if target_doc_count == 1:
        return "legacy_single_target"
    return None


def summarize_failures(case_results) -> dict[str, Any]:
    blocking_counter: Counter[str] = Counter()
    warning_counter: Counter[str] = Counter()
    capability_gap_counter: Counter[str] = Counter()
    stop_reason_counter: Counter[str] = Counter()
    final_status_counter: Counter[str] = Counter()
    failure_reason_counter: Counter[str] = Counter()
    target_match_mode_counter: Counter[str] = Counter()
    multi_target_failure_counter: Counter[str] = Counter()
    blocking_cases: list[dict[str, Any]] = []
    capability_gap_cases: list[dict[str, Any]] = []
    capability_gap_attempt_count = 0
    ambiguous_attempt_count = 0
    corrected_attempt_count = 0
    partial_target_hit_attempt_count = 0
    full_target_hit_attempt_count = 0
    target_coverage_rates: list[float] = []
    multi_target_failure_codes = {"MULTI_TARGET_PARTIAL_HIT", "TARGET_SET_INCOMPLETE"}

    for item in case_results:
        blocking = list(item.validation.blocking_failures or [])
        warnings = list(item.validation.warnings or [])
        capability_gaps = list(item.workflow.capability_gaps or [])
        stop_reason = str(item.workflow.stop_reason or "").strip()
        final_status = str(item.response.final_status or "").strip()
        failure_reason = str(item.analysis.failure_reason or "").strip()
        target_match_mode = _target_match_mode(item)
        target_doc_count = _target_doc_count(item)
        matched_target_count = _coerce_int(getattr(item.metrics, "matched_target_count", None))
        target_coverage_rate = _coerce_float(getattr(item.metrics, "target_coverage_rate", None))
        all_targets_hit = getattr(item.metrics, "all_targets_hit", None)
        for code in blocking:
            blocking_counter[str(code)] += 1
            if str(code) in multi_target_failure_codes:
                multi_target_failure_counter[str(code)] += 1
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
        if target_match_mode:
            target_match_mode_counter[target_match_mode] += 1
        if item.analysis.ambiguous_turn_count > 0:
            ambiguous_attempt_count += 1
        if item.analysis.correction_count > 0:
            corrected_attempt_count += 1
        if target_coverage_rate is not None:
            target_coverage_rates.append(target_coverage_rate)
        if matched_target_count is not None and matched_target_count > 0:
            if all_targets_hit is True or (target_doc_count > 0 and matched_target_count >= target_doc_count):
                full_target_hit_attempt_count += 1
            elif target_doc_count > 1:
                partial_target_hit_attempt_count += 1
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
                    "target_match_mode": target_match_mode,
                    "target_doc_count": target_doc_count,
                    "matched_target_count": matched_target_count,
                    "target_coverage_rate": target_coverage_rate,
                    "all_targets_hit": all_targets_hit if isinstance(all_targets_hit, bool) else None,
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
                    "target_match_mode": target_match_mode,
                    "target_doc_count": target_doc_count,
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
        "target_match_mode_counts": _sorted_counts(target_match_mode_counter),
        "multi_target_failure_counts": _sorted_counts(multi_target_failure_counter),
        "partial_target_hit_attempt_count": partial_target_hit_attempt_count,
        "full_target_hit_attempt_count": full_target_hit_attempt_count,
        "min_target_coverage_rate": (
            round(min(target_coverage_rates), 6) if target_coverage_rates else None
        ),
        "max_target_coverage_rate": (
            round(max(target_coverage_rates), 6) if target_coverage_rates else None
        ),
        "ambiguous_attempt_count": ambiguous_attempt_count,
        "corrected_attempt_count": corrected_attempt_count,
        "stop_reason_counts": _sorted_counts(stop_reason_counter),
        "final_status_counts": _sorted_counts(final_status_counter),
        "blocking_cases": blocking_cases,
        "capability_gap_cases": capability_gap_cases,
    }
