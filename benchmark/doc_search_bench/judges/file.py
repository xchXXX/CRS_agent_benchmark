from __future__ import annotations

from typing import Any

from ..envs.doc_search.matchers import matches_titles
from ..utils.text_norm import normalize_text


def _assistant_text_candidates(result) -> list[str]:
    candidates: list[str] = []
    final_response = result.workflow.final_agent_response
    if isinstance(final_response, str) and final_response.strip():
        candidates.append(final_response.strip())
    for turn in result.workflow.turns:
        if not hasattr(turn, "response_type") or turn.response_type not in {"message", "documents"}:
            continue
        body = turn.response_body
        if not isinstance(body, dict):
            continue
        content = body.get("content")
        if isinstance(content, str) and content.strip():
            candidates.append(content.strip())
        elif isinstance(content, dict):
            message = content.get("message")
            summary = content.get("summary")
            if isinstance(message, str) and message.strip():
                candidates.append(message.strip())
            if isinstance(summary, str) and summary.strip():
                candidates.append(summary.strip())
    for message in result.workflow.messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").lower() not in {"assistant", "agent"}:
            continue
        if str(message.get("message_type") or "").lower() == "ask_user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            candidates.append(content.strip())
    deduped: list[str] = []
    seen = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _text_matches_titles(text: str, accepted_titles: list[str]) -> bool:
    normalized_text = normalize_text(text)
    for title in accepted_titles:
        normalized_title = normalize_text(title)
        if normalized_title and normalized_title in normalized_text:
            return True
    return False


def _text_outputs_pass(texts: list[str], outputs: list[str]) -> bool | None:
    if not outputs:
        return None
    normalized_texts = [normalize_text(text) for text in texts if text]
    if not normalized_texts:
        return False
    for output in outputs:
        normalized_output = normalize_text(output)
        if not any(normalized_output and normalized_output in text for text in normalized_texts):
            return False
    return True


def judge_file(task, result) -> dict[str, Any]:
    docs = result.prediction.top_k_documents or []
    accepted_titles = task.accepted_titles
    is_positive = bool(accepted_titles)
    blocking_failures: list[str] = []
    warnings: list[str] = []
    assistant_texts = _assistant_text_candidates(result)
    output_pass = _text_outputs_pass(assistant_texts, task.outputs)
    root_blockers = set(result.validation.blocking_failures or [])
    scoring_blocked = bool(
        root_blockers.intersection(
            {
                "SCHEMA_INVALID",
                "HTTP_OR_RUNTIME_ERROR",
                "CAPABILITY_GAP_PRESENT",
                "ASK_USER_ROUNDS_INSUFFICIENT",
                "OCR_CONTEXT_MISSING",
                "EXPECTED_DOCUMENTS_RESPONSE",
            }
        )
    )

    matched_rank: int | None = None
    if scoring_blocked:
        recall_hit = False if is_positive else len(docs) == 0
        hit_at_1 = False
        hit_at_3 = False
        mrr = 0.0
    elif is_positive:
        if not docs:
            if not assistant_texts:
                blocking_failures.append("NO_PREDICTED_DOCUMENTS")
        for idx, doc in enumerate(docs[: task.top_k], start=1):
            if matches_titles(doc.__dict__, accepted_titles):
                matched_rank = idx
                break
        if matched_rank is None and assistant_texts:
            for text in assistant_texts:
                if _text_matches_titles(text, accepted_titles):
                    matched_rank = 1
                    warnings.append("TEXT_ONLY_FILE_MATCH")
                    break
        if matched_rank is None:
            blocking_failures.append("FILE_RECALL_MISS")
        elif matched_rank > 1:
            warnings.append("RANKING_MISS")
    else:
        if docs:
            blocking_failures.append("NOISE_FALSE_POSITIVE")

        recall_hit = len(docs) == 0
        hit_at_1 = False
        hit_at_3 = False
        mrr = 0.0

    if not scoring_blocked and is_positive:
        recall_hit = bool(matched_rank is not None)
        hit_at_1 = matched_rank == 1
        hit_at_3 = matched_rank is not None and matched_rank <= 3
        mrr = 0.0 if matched_rank is None else round(1.0 / matched_rank, 6)
    if output_pass is False:
        warnings.append("OUTPUT_TEXT_MISS")

    return {
        "is_positive": is_positive,
        "matched_rank": matched_rank,
        "recall_hit": recall_hit,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "mrr": mrr,
        "output_pass": output_pass,
        "blocking_failures": sorted(set(blocking_failures)),
        "warnings": sorted(set(warnings)),
        "pass": recall_hit and not blocking_failures,
    }


def aggregate_file_reports(case_results, threshold: float) -> dict[str, Any]:
    total = len(case_results)
    positive_results = [item for item in case_results if item.task_metadata.accepted_titles]
    negative_results = [item for item in case_results if not item.task_metadata.accepted_titles]

    positive_total = len(positive_results)
    negative_total = len(negative_results)
    positive_hits = sum(1 for item in positive_results if item.metrics.recall_hit)
    negative_pass_count = sum(
        1 for item in negative_results if "NOISE_FALSE_POSITIVE" not in item.validation.blocking_failures
    )
    blocking_count = sum(1 for item in case_results if item.validation.blocking_failures)
    hit_at_1_rate = (
        0.0 if positive_total == 0 else sum(1 for item in positive_results if item.metrics.hit_at_1) / positive_total
    )
    hit_at_3_rate = (
        0.0 if positive_total == 0 else sum(1 for item in positive_results if item.metrics.hit_at_3) / positive_total
    )
    avg_mrr = 0.0 if positive_total == 0 else sum(item.metrics.mrr for item in positive_results) / positive_total
    recall_rate = 1.0 if positive_total == 0 else positive_hits / positive_total
    negative_pass_rate = 1.0 if negative_total == 0 else negative_pass_count / negative_total
    output_case_total = sum(1 for item in case_results if item.task_metadata.outputs)
    output_pass_count = 0
    for item in case_results:
        if not item.task_metadata.outputs:
            continue
        warning_codes = set(item.validation.warnings or [])
        if "OUTPUT_TEXT_MISS" not in warning_codes:
            output_pass_count += 1

    return {
        "pass": blocking_count == 0 and recall_rate >= threshold and negative_pass_rate >= 1.0,
        "threshold": threshold,
        "total_cases": total,
        "positive_cases": positive_total,
        "negative_cases": negative_total,
        "blocking_case_count": blocking_count,
        "recall_rate": round(recall_rate, 6),
        "negative_pass_rate": round(negative_pass_rate, 6),
        "hit_at_1_rate": round(hit_at_1_rate, 6),
        "hit_at_3_rate": round(hit_at_3_rate, 6),
        "avg_mrr": round(avg_mrr, 6),
        "output_check_cases": output_case_total,
        "output_pass_rate": None if output_case_total == 0 else round(output_pass_count / output_case_total, 6),
    }
