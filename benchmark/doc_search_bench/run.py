from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .envs.doc_search.env import DocSearchBenchmarkEnv
from .envs.doc_search.rules import BENCHMARK_SLUG, DEFAULT_TIMEOUT_MS, DEFAULT_TOP_K, DEFAULT_USER_STRATEGY, SPLITS
from .envs.doc_search.tasks_dev import TASK_SUITES as DEV_TASK_SUITES
from .envs.doc_search.tasks_test import TASK_SUITES as TEST_TASK_SUITES
from .envs.doc_search.tasks_train import TASK_SUITES as TRAIN_TASK_SUITES
from .judges.failure import summarize_failures
from .judges.file import aggregate_file_reports
from .judges.page import aggregate_page_reports
from .runtime_prep import (
    DEFAULT_DOC_SEARCH_WARMUP_TIMEOUT_MS,
    ensure_local_redis_running,
    select_fast_smoke_suites,
    warmup_doc_search,
)
from .types import CaseRunResult, RunConfig, TaskSuite
from .user_model_defaults import apply_backend_llm_env_defaults, resolve_user_model_defaults
from .user import get_user_strategy, warmup_user_model


REQUEST_MODE_CHOICES = ("doc_search", "auto")
DEFAULT_REQUEST_MODE = "doc_search"
FULL_RUN_RECOMMENDED_TIMEOUT_MS = 1200000


TASK_SUITES_BY_SPLIT = {
    "train": TRAIN_TASK_SUITES,
    "dev": DEV_TASK_SUITES,
    "test": TEST_TASK_SUITES,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id(split: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{split}-{stamp}"


def repo_benchmark_root() -> Path:
    return Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def optional_env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return int(value)


def parse_args() -> argparse.Namespace:
    resolved_user_defaults = resolve_user_model_defaults()
    parser = argparse.ArgumentParser(description="Run the CRS doc_search benchmark.")
    parser.add_argument("--split", required=True, choices=SPLITS, help="Which split to run")
    parser.add_argument("--suite", action="append", default=[], help="Optional suite_id filter, repeatable")
    parser.add_argument("--case-id", action="append", default=[], help="Optional case_id filter, repeatable")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BENCHMARK_BASE_URL"),
        help="Base URL of the tested service; can also be set via BENCHMARK_BASE_URL",
    )
    parser.add_argument(
        "--app-token",
        default=os.environ.get("BENCHMARK_APP_TOKEN"),
        help="Optional app token; can also be set via BENCHMARK_APP_TOKEN",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=int(os.environ.get("BENCHMARK_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS))),
        help="HTTP timeout in milliseconds; smoke建议240000，完整回归建议1200000",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=int(os.environ.get("BENCHMARK_TOP_K", str(DEFAULT_TOP_K))),
        help="Top K to request from the service",
    )
    parser.add_argument(
        "--request-mode",
        choices=REQUEST_MODE_CHOICES,
        default=os.environ.get("BENCHMARK_REQUEST_MODE", DEFAULT_REQUEST_MODE),
        help=(
            "Mode sent to /chat/completions for benchmark initial requests. "
            "Default doc_search keeps the benchmark on the document-search path; use auto only for router checks."
        ),
    )
    parser.add_argument(
        "--max-attempts-per-case",
        type=int,
        default=optional_env_int("BENCHMARK_MAX_ATTEMPTS_PER_CASE"),
        help=(
            "Optional cap for attempts per case. Use 1 for quick smoke runs; "
            "omit it to use each fixture's case_repeat_count."
        ),
    )
    parser.add_argument(
        "--user-strategy",
        default=os.environ.get("BENCHMARK_USER_STRATEGY", DEFAULT_USER_STRATEGY),
        help="User strategy name for ask_user structured decisions, aligned with tau-bench naming",
    )
    parser.add_argument(
        "--user-model",
        default=os.environ.get("BENCHMARK_USER_MODEL", resolved_user_defaults.model),
        help=(
            "Model used by the AI simulated user for ask_user structured decisions; "
            "default aligns with backend openrouter_clarify_model -> agent_model"
        ),
    )
    parser.add_argument(
        "--user-provider",
        default=os.environ.get("BENCHMARK_USER_PROVIDER", resolved_user_defaults.provider),
        help=(
            "Optional LiteLLM provider used by the AI simulated user; "
            "default follows the resolved backend clarify model when needed"
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Legacy label retained for compatibility; no longer changes report filenames",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Optional file-level threshold override",
    )
    parser.add_argument(
        "--smoke-fast",
        action="store_true",
        help="Prefer fast smoke cases by keeping only scenario=normal when no explicit case-id is provided",
    )
    parser.add_argument(
        "--skip-redis-bootstrap",
        action="store_true",
        help="Skip best-effort local Redis bootstrap before benchmark run",
    )
    parser.add_argument(
        "--skip-doc-search-warmup",
        action="store_true",
        help="Skip best-effort doc_search warmup request before benchmark run",
    )
    return parser.parse_args()


def parse_analyze_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize failures from a benchmark score report.")
    parser.add_argument("report", help="Path to a *.score.json report")
    return parser.parse_args()


def filter_suites(suites: list[TaskSuite], suite_filters: list[str], case_filters: list[str]) -> list[TaskSuite]:
    filtered: list[TaskSuite] = []
    suite_filter_set = {item for item in suite_filters if item}
    case_filter_set = {item for item in case_filters if item}
    for suite in suites:
        if suite_filter_set and suite.suite_id not in suite_filter_set:
            continue
        if not case_filter_set:
            filtered.append(suite)
            continue
        cases = [case for case in suite.cases if case.case_id in case_filter_set]
        if not cases:
            continue
        filtered.append(
            TaskSuite(
                split=suite.split,
                suite_id=suite.suite_id,
                layer=suite.layer,
                acceptance_threshold=suite.acceptance_threshold,
                source_files=list(suite.source_files),
                cases=cases,
                legacy_source_split=suite.legacy_source_split,
            )
        )
    return filtered


def _rate(numerator: float, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)


def build_case_rollups(case_results: list[CaseRunResult]) -> list[dict[str, Any]]:
    grouped_results: dict[tuple[str, str, str], list[CaseRunResult]] = defaultdict(list)
    for item in case_results:
        grouped_results[(item.split, item.suite_id, item.case_id)].append(item)

    case_rollups: list[dict[str, Any]] = []
    for group_key in sorted(grouped_results):
        attempts = sorted(grouped_results[group_key], key=lambda item: item.attempt_index)
        sample = attempts[0]
        attempt_count = len(attempts)
        is_positive = bool(sample.task_metadata.accepted_titles)
        pass_attempt_count = sum(1 for item in attempts if not item.validation.blocking_failures)
        recall_hit_count = sum(1 for item in attempts if item.metrics.recall_hit)
        hit_at_1_count = sum(1 for item in attempts if item.metrics.hit_at_1)
        hit_at_3_count = sum(1 for item in attempts if item.metrics.hit_at_3)
        mrr_total = sum(item.metrics.mrr for item in attempts)
        negative_pass_attempt_count = (
            sum(1 for item in attempts if "NOISE_FALSE_POSITIVE" not in item.validation.blocking_failures)
            if not is_positive
            else None
        )
        output_check_attempt_count = sum(1 for item in attempts if item.task_metadata.outputs)
        output_pass_attempt_count = sum(
            1
            for item in attempts
            if item.task_metadata.outputs and "OUTPUT_TEXT_MISS" not in set(item.validation.warnings or [])
        )
        page_eligible_attempts = [item for item in attempts if item.metrics.page_hit_at_k is not None]
        page_eligible_attempt_count = len(page_eligible_attempts)
        min_page_distances = [
            item.metrics.min_page_distance
            for item in page_eligible_attempts
            if item.metrics.min_page_distance is not None
        ]

        blocking_counter: Counter[str] = Counter()
        warning_counter: Counter[str] = Counter()
        capability_gap_counter: Counter[str] = Counter()
        stop_reason_counter: Counter[str] = Counter()
        final_status_counter: Counter[str] = Counter()
        failure_reason_counter: Counter[str] = Counter()
        attempt_summaries: list[dict[str, Any]] = []
        capability_gap_attempt_count = 0
        turn_count_total = 0
        correction_total = 0
        ambiguous_total = 0
        final_hit_attempt_count = 0
        latency_total = 0.0
        latency_count = 0

        for item in attempts:
            blocking = list(item.validation.blocking_failures or [])
            warnings = list(item.validation.warnings or [])
            capability_gaps = list(item.workflow.capability_gaps or [])
            stop_reason = str(item.workflow.stop_reason or "").strip()
            final_status = str(item.response.final_status or "").strip()
            failure_reason = str(item.analysis.failure_reason or "").strip()
            duration_ms = item.execution.duration_ms

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
            if capability_gaps:
                capability_gap_attempt_count += 1
            turn_count_total += item.analysis.turn_count
            correction_total += item.analysis.correction_count
            ambiguous_total += item.analysis.ambiguous_turn_count
            if item.analysis.final_hit:
                final_hit_attempt_count += 1
            if duration_ms is not None:
                latency_total += float(duration_ms)
                latency_count += 1

            attempt_summaries.append(
                {
                    "attempt_index": item.attempt_index,
                    "final_hit": item.analysis.final_hit,
                    "turn_count": item.analysis.turn_count,
                    "duration_ms": duration_ms,
                    "correction_count": item.analysis.correction_count,
                    "ambiguous_turn_count": item.analysis.ambiguous_turn_count,
                    "failure_reason": item.analysis.failure_reason,
                    "final_status": item.response.final_status,
                    "stop_reason": item.workflow.stop_reason,
                    "blocking_failures": blocking,
                    "capability_gaps": capability_gaps,
                }
            )

        case_rollups.append(
            {
                "case_id": sample.case_id,
                "suite_id": sample.suite_id,
                "split": sample.split,
                "layer": sample.layer,
                "legacy_source_split": sample.task_metadata.legacy_source_split,
                "legacy_source_layer": sample.task_metadata.legacy_source_layer,
                "interaction_mode": sample.task_metadata.interaction_mode,
                "page_goal_mode": sample.task_metadata.page_goal_mode,
                "is_positive": is_positive,
                "attempt_count": attempt_count,
                "pass_attempt_count": pass_attempt_count,
                "pass_attempt_rate": _rate(pass_attempt_count, attempt_count),
                "all_attempts_pass": pass_attempt_count == attempt_count,
                "any_attempt_pass": pass_attempt_count > 0,
                "recall_hit_rate": _rate(recall_hit_count, attempt_count),
                "hit_at_1_rate": _rate(hit_at_1_count, attempt_count),
                "hit_at_3_rate": _rate(hit_at_3_count, attempt_count),
                "avg_mrr": round(mrr_total / attempt_count, 6),
                "negative_pass_attempt_count": negative_pass_attempt_count,
                "negative_pass_attempt_rate": (
                    _rate(negative_pass_attempt_count, attempt_count)
                    if negative_pass_attempt_count is not None
                    else None
                ),
                "output_check_attempt_count": output_check_attempt_count,
                "output_pass_attempt_count": output_pass_attempt_count,
                "output_pass_attempt_rate": (
                    _rate(output_pass_attempt_count, output_check_attempt_count)
                    if output_check_attempt_count > 0
                    else None
                ),
                "page_eligible_attempt_count": page_eligible_attempt_count,
                "page_hit_at_1_rate": _rate(
                    sum(1 for item in page_eligible_attempts if item.metrics.page_hit_at_1),
                    page_eligible_attempt_count,
                ),
                "page_hit_at_k_rate": _rate(
                    sum(1 for item in page_eligible_attempts if item.metrics.page_hit_at_k),
                    page_eligible_attempt_count,
                ),
                "exact_page_hit_rate": _rate(
                    sum(1 for item in page_eligible_attempts if item.metrics.exact_page_hit),
                    page_eligible_attempt_count,
                ),
                "page_range_overlap_rate": _rate(
                    sum(1 for item in page_eligible_attempts if item.metrics.page_range_overlap_hit),
                    page_eligible_attempt_count,
                ),
                "avg_min_page_distance": (
                    round(sum(min_page_distances) / len(min_page_distances), 6)
                    if min_page_distances
                    else None
                ),
                "final_hit_attempt_count": final_hit_attempt_count,
                "avg_turn_count": round(turn_count_total / attempt_count, 6),
                "avg_latency_ms": _round_or_none(latency_total / latency_count) if latency_count > 0 else None,
                "avg_correction_count": round(correction_total / attempt_count, 6),
                "avg_ambiguous_turn_count": round(ambiguous_total / attempt_count, 6),
                "capability_gap_attempt_count": capability_gap_attempt_count,
                "capability_gap_counts": _sorted_counts(capability_gap_counter),
                "failure_reason_counts": _sorted_counts(failure_reason_counter),
                "stop_reason_counts": _sorted_counts(stop_reason_counter),
                "final_status_counts": _sorted_counts(final_status_counter),
                "blocking_failure_counts": _sorted_counts(blocking_counter),
                "warning_counts": _sorted_counts(warning_counter),
                "attempts": attempt_summaries,
            }
        )
    return case_rollups


def aggregate_attempt_efficiency(case_results: list[CaseRunResult]) -> dict[str, Any]:
    total_attempts = len(case_results)
    if total_attempts == 0:
        return {
            "count_basis": "attempt",
            "attempt_count": 0,
            "avg_turn_count": None,
        }
    return {
        "count_basis": "attempt",
        "attempt_count": total_attempts,
        "avg_turn_count": round(sum(item.analysis.turn_count for item in case_results) / total_attempts, 6),
    }


def aggregate_case_rollup_files(case_rollups: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    total_cases = len(case_rollups)
    positive_rollups = [item for item in case_rollups if item["is_positive"]]
    negative_rollups = [item for item in case_rollups if not item["is_positive"]]
    positive_total = len(positive_rollups)
    negative_total = len(negative_rollups)
    blocking_case_count = sum(1 for item in case_rollups if item["blocking_failure_counts"])
    stable_pass_case_count = sum(1 for item in case_rollups if item["all_attempts_pass"])
    any_pass_case_count = sum(1 for item in case_rollups if item["any_attempt_pass"])
    avg_attempt_pass_rate = _rate(sum((item["pass_attempt_rate"] or 0.0) for item in case_rollups), total_cases)
    recall_rate = 1.0 if positive_total == 0 else sum(item["recall_hit_rate"] or 0.0 for item in positive_rollups) / positive_total
    negative_pass_rate = (
        1.0
        if negative_total == 0
        else sum(item["negative_pass_attempt_rate"] or 0.0 for item in negative_rollups) / negative_total
    )
    hit_at_1_rate = 0.0 if positive_total == 0 else sum(item["hit_at_1_rate"] or 0.0 for item in positive_rollups) / positive_total
    hit_at_3_rate = 0.0 if positive_total == 0 else sum(item["hit_at_3_rate"] or 0.0 for item in positive_rollups) / positive_total
    avg_mrr = 0.0 if positive_total == 0 else sum(item["avg_mrr"] for item in positive_rollups) / positive_total

    output_rollups = [item for item in case_rollups if item["output_check_attempt_count"] > 0]
    output_check_cases = len(output_rollups)
    output_pass_rate = (
        None
        if output_check_cases == 0
        else round(
            sum(item["output_pass_attempt_rate"] or 0.0 for item in output_rollups) / output_check_cases,
            6,
        )
    )

    return {
        "count_basis": "unique_case",
        "pass": blocking_case_count == 0 and recall_rate >= threshold and negative_pass_rate >= 1.0,
        "threshold": threshold,
        "total_cases": total_cases,
        "positive_cases": positive_total,
        "negative_cases": negative_total,
        "blocking_case_count": blocking_case_count,
        "stable_pass_case_count": stable_pass_case_count,
        "stable_pass_case_rate": _rate(stable_pass_case_count, total_cases),
        "any_pass_case_count": any_pass_case_count,
        "any_pass_case_rate": _rate(any_pass_case_count, total_cases),
        "avg_attempt_pass_rate": avg_attempt_pass_rate,
        "recall_rate": round(recall_rate, 6),
        "negative_pass_rate": round(negative_pass_rate, 6),
        "hit_at_1_rate": round(hit_at_1_rate, 6),
        "hit_at_3_rate": round(hit_at_3_rate, 6),
        "avg_mrr": round(avg_mrr, 6),
        "output_check_cases": output_check_cases,
        "output_pass_rate": output_pass_rate,
    }


def aggregate_case_rollup_efficiency(case_rollups: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = len(case_rollups)
    if total_cases == 0:
        return {
            "count_basis": "unique_case",
            "total_cases": 0,
            "avg_turn_count": None,
        }
    return {
        "count_basis": "unique_case",
        "total_cases": total_cases,
        "avg_turn_count": round(sum(item["avg_turn_count"] or 0.0 for item in case_rollups) / total_cases, 6),
    }


def aggregate_case_rollup_page(case_rollups: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = len(case_rollups)
    disabled_cases = sum(1 for item in case_rollups if item["page_goal_mode"] == "disabled")
    shadow_cases = sum(1 for item in case_rollups if item["page_goal_mode"] == "shadow")
    required_cases = sum(1 for item in case_rollups if item["page_goal_mode"] == "required")
    eligible_rollups = [item for item in case_rollups if item["page_eligible_attempt_count"] > 0]
    eligible_cases = len(eligible_rollups)
    shadow_eligible_cases = sum(1 for item in eligible_rollups if item["page_goal_mode"] == "shadow")
    required_eligible_cases = sum(1 for item in eligible_rollups if item["page_goal_mode"] == "required")

    if eligible_cases == 0:
        return {
            "count_basis": "unique_case",
            "total_cases": total_cases,
            "eligible_cases": 0,
            "disabled_cases": disabled_cases,
            "shadow_cases": shadow_cases,
            "required_cases": required_cases,
            "shadow_eligible_cases": 0,
            "required_eligible_cases": 0,
            "page_hit_at_1_rate": None,
            "page_hit_at_k_rate": None,
            "exact_page_hit_rate": None,
            "page_range_overlap_rate": None,
            "avg_min_page_distance": None,
        }

    distances = [item["avg_min_page_distance"] for item in eligible_rollups if item["avg_min_page_distance"] is not None]
    return {
        "count_basis": "unique_case",
        "total_cases": total_cases,
        "eligible_cases": eligible_cases,
        "disabled_cases": disabled_cases,
        "shadow_cases": shadow_cases,
        "required_cases": required_cases,
        "shadow_eligible_cases": shadow_eligible_cases,
        "required_eligible_cases": required_eligible_cases,
        "page_hit_at_1_rate": _rate(sum(item["page_hit_at_1_rate"] or 0.0 for item in eligible_rollups), eligible_cases),
        "page_hit_at_k_rate": _rate(sum(item["page_hit_at_k_rate"] or 0.0 for item in eligible_rollups), eligible_cases),
        "exact_page_hit_rate": _rate(sum(item["exact_page_hit_rate"] or 0.0 for item in eligible_rollups), eligible_cases),
        "page_range_overlap_rate": _rate(
            sum(item["page_range_overlap_rate"] or 0.0 for item in eligible_rollups),
            eligible_cases,
        ),
        "avg_min_page_distance": round(sum(distances) / len(distances), 6) if distances else None,
    }


def aggregate_attempt_performance(case_results: list[CaseRunResult]) -> dict[str, Any]:
    latencies = [float(item.execution.duration_ms) for item in case_results if item.execution.duration_ms is not None]
    return {
        "count_basis": "attempt",
        "attempt_count": len(case_results),
        "avg_latency_ms": _round_or_none(sum(latencies) / len(latencies)) if latencies else None,
    }


def aggregate_case_rollup_performance(case_rollups: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = len(case_rollups)
    latencies = [float(item["avg_latency_ms"]) for item in case_rollups if item["avg_latency_ms"] is not None]
    return {
        "count_basis": "unique_case",
        "total_cases": total_cases,
        "avg_latency_ms": _round_or_none(sum(latencies) / len(latencies)) if latencies else None,
    }


def build_dimension_summary(
    *,
    file_summary: dict[str, Any],
    page_summary: dict[str, Any],
    efficiency_summary: dict[str, Any],
    performance_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "functional": {
            "official": {
                "doc_recall_at_k": file_summary.get("recall_rate"),
            },
            "shadow": {
                "page_recall_at_k": page_summary.get("page_hit_at_k_rate"),
            },
        },
        "ranking": {
            "official": {
                "gold_doc_hit_at_1": file_summary.get("hit_at_1_rate"),
                "gold_doc_hit_at_3": file_summary.get("hit_at_3_rate"),
                "gold_doc_mrr": file_summary.get("avg_mrr"),
            },
            "shadow": {
                "gold_page_hit_at_1": page_summary.get("page_hit_at_1_rate"),
                "gold_page_hit_at_k": page_summary.get("page_hit_at_k_rate"),
            },
        },
        "interaction_efficiency": {
            "official": {
                "avg_turn_count": efficiency_summary.get("avg_turn_count"),
            },
        },
        "system_performance": {
            "official": {
                "avg_latency_ms": performance_summary.get("avg_latency_ms"),
            },
        },
    }


def aggregate_case_rollup_failures(case_rollups: list[dict[str, Any]]) -> dict[str, Any]:
    blocking_counter: Counter[str] = Counter()
    warning_counter: Counter[str] = Counter()
    capability_gap_counter: Counter[str] = Counter()
    stop_reason_counter: Counter[str] = Counter()
    final_status_counter: Counter[str] = Counter()
    failure_reason_counter: Counter[str] = Counter()
    blocking_cases: list[dict[str, Any]] = []
    capability_gap_cases: list[dict[str, Any]] = []

    for item in case_rollups:
        blocking_codes = sorted(item["blocking_failure_counts"].keys())
        warning_codes = sorted(item["warning_counts"].keys())
        capability_gap_codes = sorted(item["capability_gap_counts"].keys())
        failure_reason_codes = sorted(item["failure_reason_counts"].keys())
        stop_reasons = sorted(item["stop_reason_counts"].keys())
        final_statuses = sorted(item["final_status_counts"].keys())

        for code in blocking_codes:
            blocking_counter[code] += 1
        for code in warning_codes:
            warning_counter[code] += 1
        for code in capability_gap_codes:
            capability_gap_counter[code] += 1
        for code in failure_reason_codes:
            failure_reason_counter[code] += 1
        for code in stop_reasons:
            stop_reason_counter[code] += 1
        for code in final_statuses:
            final_status_counter[code] += 1

        if blocking_codes:
            blocking_cases.append(
                {
                    "case_id": item["case_id"],
                    "suite_id": item["suite_id"],
                    "split": item["split"],
                    "layer": item["layer"],
                    "attempt_count": item["attempt_count"],
                    "pass_attempt_count": item["pass_attempt_count"],
                    "pass_attempt_rate": item["pass_attempt_rate"],
                    "blocking_failures": blocking_codes,
                    "failure_reasons": failure_reason_codes,
                    "capability_gaps": capability_gap_codes,
                    "stop_reason_counts": item["stop_reason_counts"],
                    "final_status_counts": item["final_status_counts"],
                }
            )
        if capability_gap_codes:
            capability_gap_cases.append(
                {
                    "case_id": item["case_id"],
                    "suite_id": item["suite_id"],
                    "split": item["split"],
                    "layer": item["layer"],
                    "attempt_count": item["attempt_count"],
                    "capability_gap_attempt_count": item["capability_gap_attempt_count"],
                    "capability_gaps": capability_gap_codes,
                    "failure_reasons": failure_reason_codes,
                    "blocking_failures": blocking_codes,
                    "stop_reason_counts": item["stop_reason_counts"],
                    "final_status_counts": item["final_status_counts"],
                }
            )

    return {
        "count_basis": "unique_case",
        "blocking_failure_counts": _sorted_counts(blocking_counter),
        "warning_counts": _sorted_counts(warning_counter),
        "capability_gap_counts": _sorted_counts(capability_gap_counter),
        "capability_gap_case_count": len(capability_gap_cases),
        "failure_reason_counts": _sorted_counts(failure_reason_counter),
        "stop_reason_counts": _sorted_counts(stop_reason_counter),
        "final_status_counts": _sorted_counts(final_status_counter),
        "blocking_cases": blocking_cases,
        "capability_gap_cases": capability_gap_cases,
    }


def build_actual_report(
    *,
    split: str,
    run_id: str,
    run_output_dir: str | None,
    runtime_log_path: str | None,
    user_strategy: str,
    user_model: str | None,
    user_provider: str | None,
    request_mode: str,
    max_attempts_per_case: int | None,
    suites: list[TaskSuite],
    case_results: list[CaseRunResult],
) -> dict[str, Any]:
    case_rollups = build_case_rollups(case_results)
    return {
        "benchmark_slug": BENCHMARK_SLUG,
        "run_id": run_id,
        "generated_at": now_iso(),
        "split": split,
        "run_output_dir": run_output_dir,
        "runtime_log_path": runtime_log_path,
        "user_strategy": user_strategy,
        "user_model": user_model,
        "user_provider": user_provider,
        "request_mode": request_mode,
        "max_attempts_per_case": max_attempts_per_case,
        "suite_ids": [suite.suite_id for suite in suites],
        "unique_case_count": len(case_rollups),
        "attempt_count": len(case_results),
        "case_count": len(case_results),
        "case_rollups": case_rollups,
        "cases": [case.to_dict() for case in case_results],
    }


def build_score_report(
    *,
    split: str,
    run_id: str,
    run_output_dir: str | None,
    runtime_log_path: str | None,
    user_strategy: str,
    user_model: str | None,
    user_provider: str | None,
    request_mode: str,
    max_attempts_per_case: int | None,
    suites: list[TaskSuite],
    case_results: list[CaseRunResult],
    threshold_override: float | None,
) -> dict[str, Any]:
    threshold_by_suite = {
        suite.suite_id: threshold_override if threshold_override is not None else suite.acceptance_threshold
        for suite in suites
    }
    threshold = threshold_override if threshold_override is not None else (
        min(threshold_by_suite.values()) if threshold_by_suite else 1.0
    )

    suite_results_map: dict[str, list[CaseRunResult]] = defaultdict(list)
    for item in case_results:
        suite_results_map[item.suite_id].append(item)

    case_rollups = build_case_rollups(case_results)
    suite_summaries = []
    for suite in suites:
        suite_case_results = suite_results_map.get(suite.suite_id, [])
        suite_case_rollups = build_case_rollups(suite_case_results)
        suite_attempt_efficiency = aggregate_attempt_efficiency(suite_case_results)
        suite_case_efficiency = aggregate_case_rollup_efficiency(suite_case_rollups)
        suite_attempt_performance = aggregate_attempt_performance(suite_case_results)
        suite_case_performance = aggregate_case_rollup_performance(suite_case_rollups)
        attempt_level_summary = {
            "file": aggregate_file_reports(suite_case_results, threshold_by_suite[suite.suite_id]),
            "page": aggregate_page_reports(suite_case_results),
            "efficiency": suite_attempt_efficiency,
            "performance": suite_attempt_performance,
            "failures": summarize_failures(suite_case_results),
        }
        case_level_summary = {
            "file": aggregate_case_rollup_files(suite_case_rollups, threshold_by_suite[suite.suite_id]),
            "page": aggregate_case_rollup_page(suite_case_rollups),
            "efficiency": suite_case_efficiency,
            "performance": suite_case_performance,
            "failures": aggregate_case_rollup_failures(suite_case_rollups),
        }
        suite_official_gate = {
            "level": "attempt_level",
            "pass": bool(attempt_level_summary["file"].get("pass")),
            "threshold": threshold_by_suite[suite.suite_id],
            "capability_gap_enters_fail": True,
        }
        suite_summaries.append(
            {
                "suite_id": suite.suite_id,
                "split": suite.split,
                "layer": suite.layer,
                "legacy_source_split": suite.legacy_source_split,
                "attempt_count": len(suite_case_results),
                "unique_case_count": len(suite_case_rollups),
                "case_count": len(suite_case_results),
                "threshold": threshold_by_suite[suite.suite_id],
                "official_gate": suite_official_gate,
                "attempt_level": attempt_level_summary,
                "case_level": case_level_summary,
                "dimensions": {
                    "attempt_level": build_dimension_summary(
                        file_summary=attempt_level_summary["file"],
                        page_summary=attempt_level_summary["page"],
                        efficiency_summary=attempt_level_summary["efficiency"],
                        performance_summary=attempt_level_summary["performance"],
                    ),
                    "case_level": build_dimension_summary(
                        file_summary=case_level_summary["file"],
                        page_summary=case_level_summary["page"],
                        efficiency_summary=case_level_summary["efficiency"],
                        performance_summary=case_level_summary["performance"],
                    ),
                },
                "file": attempt_level_summary["file"],
                "page": attempt_level_summary["page"],
                "efficiency": attempt_level_summary["efficiency"],
                "performance": attempt_level_summary["performance"],
                "failures": attempt_level_summary["failures"],
            }
        )

    attempt_efficiency = aggregate_attempt_efficiency(case_results)
    case_efficiency = aggregate_case_rollup_efficiency(case_rollups)
    attempt_performance = aggregate_attempt_performance(case_results)
    case_performance = aggregate_case_rollup_performance(case_rollups)
    attempt_level_summary = {
        "file": aggregate_file_reports(case_results, threshold),
        "page": aggregate_page_reports(case_results),
        "efficiency": attempt_efficiency,
        "performance": attempt_performance,
        "failures": summarize_failures(case_results),
    }
    case_level_summary = {
        "file": aggregate_case_rollup_files(case_rollups, threshold),
        "page": aggregate_case_rollup_page(case_rollups),
        "efficiency": case_efficiency,
        "performance": case_performance,
        "failures": aggregate_case_rollup_failures(case_rollups),
    }
    official_gate = {
        "level": "attempt_level",
        "pass": bool(attempt_level_summary["file"].get("pass")),
        "threshold": threshold,
        "capability_gap_enters_fail": True,
    }

    return {
        "benchmark_slug": BENCHMARK_SLUG,
        "run_id": run_id,
        "generated_at": now_iso(),
        "split": split,
        "run_output_dir": run_output_dir,
        "runtime_log_path": runtime_log_path,
        "user_strategy": user_strategy,
        "user_model": user_model,
        "user_provider": user_provider,
        "request_mode": request_mode,
        "max_attempts_per_case": max_attempts_per_case,
        "unique_case_count": len(case_rollups),
        "attempt_count": len(case_results),
        "case_count": len(case_results),
        "threshold": threshold,
        "official_gate": official_gate,
        "suite_summaries": suite_summaries,
        "case_rollups": case_rollups,
        "summary": {
            "official_gate": official_gate,
            "attempt_level": attempt_level_summary,
            "case_level": case_level_summary,
            "dimensions": {
                "attempt_level": build_dimension_summary(
                    file_summary=attempt_level_summary["file"],
                    page_summary=attempt_level_summary["page"],
                    efficiency_summary=attempt_level_summary["efficiency"],
                    performance_summary=attempt_level_summary["performance"],
                ),
                "case_level": build_dimension_summary(
                    file_summary=case_level_summary["file"],
                    page_summary=case_level_summary["page"],
                    efficiency_summary=case_level_summary["efficiency"],
                    performance_summary=case_level_summary["performance"],
                ),
            },
            "file": attempt_level_summary["file"],
            "page": attempt_level_summary["page"],
            "efficiency": attempt_level_summary["efficiency"],
            "performance": attempt_level_summary["performance"],
            "failures": attempt_level_summary["failures"],
        },
        "cases": [case.to_dict() for case in case_results],
    }


def persist_reports(
    *,
    run_output_dir: Path,
    actual_report: dict[str, Any],
    score_report: dict[str, Any],
) -> tuple[Path, Path]:
    actual_path = run_output_dir / "report.actual.json"
    score_path = run_output_dir / "report.score.json"
    write_json(actual_path, actual_report)
    write_json(score_path, score_report)
    return actual_path, score_path


def main() -> int:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("BENCHMARK_BASE_URL or --base-url is required")
    if args.max_attempts_per_case is not None and args.max_attempts_per_case < 1:
        raise SystemExit("--max-attempts-per-case must be >= 1")

    apply_backend_llm_env_defaults()
    get_user_strategy(args.user_strategy)

    suites = filter_suites(TASK_SUITES_BY_SPLIT[args.split], args.suite, args.case_id)
    if args.smoke_fast and not args.case_id:
        suites = select_fast_smoke_suites(suites, split=args.split)
    if not suites:
        raise SystemExit("濠电偛澶囬崜婵嗭耿娓氣偓瀹曠姾銇愰幒鎴濊祴闂佸憡甯￠。锕傚箲閵忊剝濯?suite / case")

    config = RunConfig(
        split=args.split,
        base_url=args.base_url,
        app_token=args.app_token,
        timeout_ms=args.timeout_ms,
        top_k=args.top_k,
        request_mode=args.request_mode,
        max_attempts_per_case=args.max_attempts_per_case,
        user_strategy=args.user_strategy,
        user_model=args.user_model,
        user_provider=args.user_provider,
        output_prefix=args.output_prefix or args.split,
        suite_filters=list(args.suite),
        case_filters=list(args.case_id),
        threshold_override=args.threshold,
    )

    benchmark_root = repo_benchmark_root()
    run_id = make_run_id(args.split)
    env = DocSearchBenchmarkEnv(config=config, benchmark_root=benchmark_root, run_id=run_id)
    run_output_dir = str(env.run_root)
    runtime_log_path = str(env.runtime_logger.path)
    case_results: list[CaseRunResult] = []
    try:
        case_count = sum(len(suite.cases) for suite in suites)
        attempt_count = sum(env.effective_repeat_count(case) for suite in suites for case in suite.cases)
        env.runtime_logger.emit(
            "run_start",
            context=[("run_id", run_id), ("split", args.split)],
            result=[
                ("suite_count", len(suites)),
                ("case_count", case_count),
                ("attempt_count", attempt_count),
                ("base_url", args.base_url),
                ("request_mode", args.request_mode),
                ("max_attempts_per_case", args.max_attempts_per_case),
                ("smoke_fast", args.smoke_fast),
                ("full_run_recommended_timeout_ms", FULL_RUN_RECOMMENDED_TIMEOUT_MS),
            ],
        )
        if not args.skip_redis_bootstrap:
            redis_result = ensure_local_redis_running()
            env.runtime_logger.emit(
                "local_redis_prepare",
                context=[("run_id", run_id), ("split", args.split)],
                result=[
                    ("ready", redis_result.get("ready")),
                    ("attempted", redis_result.get("attempted")),
                    ("host", redis_result.get("host")),
                    ("port", redis_result.get("port")),
                    ("method", redis_result.get("method")),
                ],
                detail=(
                    "；".join(redis_result.get("errors", []))
                    if isinstance(redis_result.get("errors"), list) and redis_result.get("errors")
                    else None
                ),
            )
        if not args.skip_doc_search_warmup:
            warmup_result = warmup_doc_search(
                base_url=args.base_url,
                app_token=args.app_token,
                timeout_ms=max(args.timeout_ms, DEFAULT_DOC_SEARCH_WARMUP_TIMEOUT_MS),
            )
            env.runtime_logger.emit(
                "doc_search_warmup",
                context=[("run_id", run_id), ("split", args.split)],
                result=[
                    ("ok", warmup_result.get("ok")),
                    ("http_status", warmup_result.get("http_status")),
                    ("elapsed_ms", warmup_result.get("elapsed_ms")),
                    ("response_type", warmup_result.get("response_type")),
                    ("business", warmup_result.get("business")),
                ],
                detail=warmup_result.get("error") or warmup_result.get("response_text"),
            )
        if args.user_provider == "ollama" and args.user_model:
            env.runtime_logger.emit(
                "Ollama warmup start",
                context=[("run_id", run_id), ("split", args.split)],
                result=[("provider", args.user_provider), ("model", args.user_model)],
            )
            warmup_user_model(args.user_model, args.user_provider)
            env.runtime_logger.emit(
                "Ollama warmup complete",
                context=[("run_id", run_id), ("split", args.split)],
                result=[("provider", args.user_provider), ("model", args.user_model)],
            )
        case_results = env.run_suites(suites)

        actual_report = build_actual_report(
            split=args.split,
            run_id=run_id,
            run_output_dir=run_output_dir,
            runtime_log_path=runtime_log_path,
            user_strategy=args.user_strategy,
            user_model=args.user_model,
            user_provider=args.user_provider,
            request_mode=args.request_mode,
            max_attempts_per_case=args.max_attempts_per_case,
            suites=suites,
            case_results=case_results,
        )
        score_report = build_score_report(
            split=args.split,
            run_id=run_id,
            run_output_dir=run_output_dir,
            runtime_log_path=runtime_log_path,
            user_strategy=args.user_strategy,
            user_model=args.user_model,
            user_provider=args.user_provider,
            request_mode=args.request_mode,
            max_attempts_per_case=args.max_attempts_per_case,
            suites=suites,
            case_results=case_results,
            threshold_override=args.threshold,
        )
        actual_path, score_path = persist_reports(
            run_output_dir=env.run_root,
            actual_report=actual_report,
            score_report=score_report,
        )

        for case in case_results:
            case.artifacts.normalized_output_path = str(actual_path)
            case.artifacts.score_report_path = str(score_path)
        actual_report = build_actual_report(
            split=args.split,
            run_id=run_id,
            run_output_dir=run_output_dir,
            runtime_log_path=runtime_log_path,
            user_strategy=args.user_strategy,
            user_model=args.user_model,
            user_provider=args.user_provider,
            request_mode=args.request_mode,
            max_attempts_per_case=args.max_attempts_per_case,
            suites=suites,
            case_results=case_results,
        )
        score_report = build_score_report(
            split=args.split,
            run_id=run_id,
            run_output_dir=run_output_dir,
            runtime_log_path=runtime_log_path,
            user_strategy=args.user_strategy,
            user_model=args.user_model,
            user_provider=args.user_provider,
            request_mode=args.request_mode,
            max_attempts_per_case=args.max_attempts_per_case,
            suites=suites,
            case_results=case_results,
            threshold_override=args.threshold,
        )
        persist_reports(
            run_output_dir=env.run_root,
            actual_report=actual_report,
            score_report=score_report,
        )
        env.runtime_logger.emit(
            "reports_written",
            context=[("run_id", run_id), ("split", args.split)],
            result=[("run_dir", run_output_dir)],
            path=[
                ("actual", str(actual_path)),
                ("score", str(score_path)),
                ("runtime_log", runtime_log_path),
            ],
        )

        official_gate = score_report["official_gate"]
        summary = score_report["summary"]["file"]
        env.runtime_logger.emit(
            "run_complete",
            context=[("run_id", run_id), ("split", args.split)],
            result=[
                ("official_pass", official_gate.get("pass")),
                ("threshold", official_gate.get("threshold")),
                ("attempt_count", len(case_results)),
            ],
            payload={"pass": bool(official_gate.get("pass"))},
        )
        print(json.dumps(
            {
                "benchmark_slug": BENCHMARK_SLUG,
                "run_id": run_id,
                "split": args.split,
                "run_output_dir": run_output_dir,
                "actual_report": str(actual_path),
                "score_report": str(score_path),
                "runtime_log": runtime_log_path,
                "request_mode": args.request_mode,
                "max_attempts_per_case": args.max_attempts_per_case,
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if bool(summary.get("pass")) else 1
    except Exception as exc:
        env.runtime_logger.emit(
            "run_error",
            level="error",
            context=[("run_id", run_id), ("split", args.split)],
            result=[("exception_type", type(exc).__name__)],
            detail=str(exc),
            payload={"error": str(exc)},
        )
        raise
    finally:
        env.runtime_logger.finalize()


def analyze_failures_main() -> int:
    args = parse_analyze_args()
    report_path = Path(args.report).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report.get("summary") or {}
    official_gate = report.get("official_gate") or (
        summary.get("official_gate") if isinstance(summary, dict) else None
    )
    attempt_level = summary.get("attempt_level") if isinstance(summary, dict) else None
    case_level = summary.get("case_level") if isinstance(summary, dict) else None
    attempt_level_failures = None
    case_level_failures = None

    if isinstance(attempt_level, dict):
        attempt_level_failures = attempt_level.get("failures")
    elif isinstance(summary, dict):
        attempt_level_failures = summary.get("failures")
    if isinstance(case_level, dict):
        case_level_failures = case_level.get("failures")

    if isinstance(attempt_level_failures, dict):
        print(json.dumps(
            {
                "report_path": str(report_path),
                "official_gate": official_gate,
                "attempt_level_failures": attempt_level_failures,
                "case_level_failures": case_level_failures,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    cases = report.get("cases") or []
    failure_counts: dict[str, int] = defaultdict(int)
    warning_counts: dict[str, int] = defaultdict(int)
    capability_gap_counts: dict[str, int] = defaultdict(int)
    stop_reason_counts: dict[str, int] = defaultdict(int)
    final_status_counts: dict[str, int] = defaultdict(int)
    blocking_cases: list[dict[str, Any]] = []
    capability_gap_cases: list[dict[str, Any]] = []

    for case in cases:
        if not isinstance(case, dict):
            continue
        validation = case.get("validation") or {}
        workflow = case.get("workflow") or {}
        response = case.get("response") or {}
        blocking = validation.get("blocking_failures") or []
        warnings = validation.get("warnings") or []
        capability_gaps = workflow.get("capability_gaps") or []
        stop_reason = workflow.get("stop_reason")
        final_status = response.get("final_status")
        for code in blocking:
            failure_counts[str(code)] += 1
        for code in warnings:
            warning_counts[str(code)] += 1
        for code in capability_gaps:
            capability_gap_counts[str(code)] += 1
        if isinstance(stop_reason, str) and stop_reason.strip():
            stop_reason_counts[stop_reason.strip()] += 1
        if isinstance(final_status, str) and final_status.strip():
            final_status_counts[final_status.strip()] += 1
        if blocking:
            blocking_cases.append(
                {
                    "case_id": case.get("case_id"),
                    "attempt_index": case.get("attempt_index"),
                    "suite_id": case.get("suite_id"),
                    "split": case.get("split"),
                    "layer": case.get("layer"),
                    "blocking_failures": blocking,
                    "capability_gaps": capability_gaps,
                    "stop_reason": stop_reason,
                    "final_status": final_status,
                }
            )
        if capability_gaps:
            capability_gap_cases.append(
                {
                    "case_id": case.get("case_id"),
                    "attempt_index": case.get("attempt_index"),
                    "suite_id": case.get("suite_id"),
                    "split": case.get("split"),
                    "layer": case.get("layer"),
                    "capability_gaps": capability_gaps,
                    "blocking_failures": blocking,
                    "stop_reason": stop_reason,
                    "final_status": final_status,
                }
            )

    print(json.dumps(
        {
            "report_path": str(report_path),
            "official_gate": official_gate,
            "blocking_failure_counts": dict(sorted(failure_counts.items())),
            "warning_counts": dict(sorted(warning_counts.items())),
            "capability_gap_counts": dict(sorted(capability_gap_counts.items())),
            "stop_reason_counts": dict(sorted(stop_reason_counts.items())),
            "final_status_counts": dict(sorted(final_status_counts.items())),
            "blocking_cases": blocking_cases,
            "capability_gap_cases": capability_gap_cases,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0
