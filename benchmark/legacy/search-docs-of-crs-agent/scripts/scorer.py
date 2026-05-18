#!/usr/bin/env python3
"""Score normalized benchmark outputs against gold manifests."""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path


RECOMMEND_MARKERS = (
    "\u3010\u63a8\u8350\u3011",
    "[\u63a8\u8350]",
    "\u63a8\u8350:",
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    for marker in RECOMMEND_MARKERS:
        normalized = normalized.replace(marker.lower(), "")
    chars: list[str] = []
    for ch in normalized:
        category = unicodedata.category(ch)
        if category.startswith("Z") or category.startswith("P") or category == "Cc":
            continue
        chars.append(ch)
    return "".join(chars)


def load_cases(blob: object) -> list[dict]:
    if isinstance(blob, dict) and isinstance(blob.get("cases"), list):
        return [item for item in blob["cases"] if isinstance(item, dict)]
    if isinstance(blob, dict):
        return [blob]
    return []


def candidate_strings(doc: dict) -> list[str]:
    values = []
    for key in ("doc_title", "doc_path"):
        raw = doc.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    return values


def matches_gold(doc: dict, accepted_titles: list[str]) -> bool:
    candidates = [normalize_text(item) for item in candidate_strings(doc)]
    golds = [normalize_text(item) for item in accepted_titles if isinstance(item, str) and item.strip()]
    for candidate in candidates:
        for gold in golds:
            if candidate == gold or candidate in gold or gold in candidate:
                return True
    return False


def score_case(actual: dict, gold: dict) -> dict:
    docs = []
    if isinstance(actual.get("prediction"), dict):
        docs = actual["prediction"].get("top_k_documents") or []

    accepted_titles = [item for item in (gold.get("accepted_titles") or []) if isinstance(item, str) and item.strip()]
    preferred_title = gold.get("preferred_title")
    expected_response_type = gold.get("expected_response_type", "documents")
    threshold_k = int(gold.get("top_k", 10))
    blocking = list((actual.get("validation") or {}).get("blocking_failures") or [])
    is_positive = bool(accepted_titles)

    response_type = ((actual.get("response") or {}).get("response_type")) or ""
    if expected_response_type == "documents" and response_type != "documents":
        blocking.append("EXPECTED_DOCUMENTS_RESPONSE")
    if expected_response_type == "message_or_empty" and docs:
        blocking.append("NOISE_RETURNED_DOCUMENTS")
    if is_positive and not docs:
        blocking.append("NO_PREDICTED_DOCUMENTS")

    matched_rank = None
    if is_positive:
        for idx, doc in enumerate(docs[:threshold_k], start=1):
            if isinstance(doc, dict) and matches_gold(doc, accepted_titles):
                matched_rank = idx
                break
        recall_hit = matched_rank is not None
    else:
        recall_hit = len(docs) == 0

    hit_at_1 = matched_rank == 1
    hit_at_3 = matched_rank is not None and matched_rank <= 3
    mrr = 0.0 if matched_rank is None else 1.0 / matched_rank

    preferred_hit_at_1 = False
    if preferred_title and docs:
        first_doc = docs[0] if isinstance(docs[0], dict) else {}
        preferred_hit_at_1 = matches_gold(first_doc, [preferred_title])

    return {
        "case_id": actual.get("case_id") or gold.get("case_id"),
        "layer": actual.get("layer") or gold.get("layer"),
        "is_positive": is_positive,
        "recall_hit": recall_hit,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "mrr": round(mrr, 6),
        "preferred_hit_at_1": preferred_hit_at_1,
        "blocking_failures": sorted(set(blocking)),
        "pass": recall_hit and not blocking,
    }


def aggregate(case_reports: list[dict], threshold: float) -> dict:
    total = len(case_reports)
    positive_reports = [item for item in case_reports if item["is_positive"]]
    negative_reports = [item for item in case_reports if not item["is_positive"]]

    positive_total = len(positive_reports)
    positive_hits = sum(1 for item in positive_reports if item["recall_hit"])
    negative_total = len(negative_reports)
    negative_pass_count = sum(1 for item in negative_reports if item["pass"])

    pass_count = sum(1 for item in case_reports if item["pass"])
    blocking_count = sum(1 for item in case_reports if item["blocking_failures"])
    hit1_rate = (
        0.0 if positive_total == 0 else sum(1 for item in positive_reports if item["hit_at_1"]) / positive_total
    )
    hit3_rate = (
        0.0 if positive_total == 0 else sum(1 for item in positive_reports if item["hit_at_3"]) / positive_total
    )
    avg_mrr = 0.0 if positive_total == 0 else sum(item["mrr"] for item in positive_reports) / positive_total
    recall_rate = 1.0 if positive_total == 0 else positive_hits / positive_total
    negative_pass_rate = 1.0 if negative_total == 0 else negative_pass_count / negative_total
    passed = blocking_count == 0 and recall_rate >= threshold and negative_pass_rate >= 1.0

    return {
        "pass": passed,
        "threshold": threshold,
        "total_cases": total,
        "positive_cases": positive_total,
        "negative_cases": negative_total,
        "pass_count": pass_count,
        "blocking_case_count": blocking_count,
        "recall_rate": round(recall_rate, 6),
        "negative_pass_rate": round(negative_pass_rate, 6),
        "hit_at_1_rate": round(hit1_rate, 6),
        "hit_at_3_rate": round(hit3_rate, 6),
        "avg_mrr": round(avg_mrr, 6),
    }


def resolve_pairs(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    if args.actual and args.gold:
        pairs.append((Path(args.actual).resolve(), Path(args.gold).resolve()))
    if args.pair:
        for actual, gold in args.pair:
            pairs.append((Path(actual).resolve(), Path(gold).resolve()))
    if not pairs:
        parser.error("must provide either <actual> <gold> or one or more --pair <actual> <gold>")
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Score actual JSON against one or more gold manifest JSON files.")
    parser.add_argument("actual", nargs="?", help="Path to the produced artifact JSON")
    parser.add_argument("gold", nargs="?", help="Path to the gold artifact JSON")
    parser.add_argument(
        "--pair",
        nargs=2,
        action="append",
        metavar=("ACTUAL", "GOLD"),
        help="Append an additional actual/gold pair for aggregate scoring",
    )
    parser.add_argument("--threshold", type=float, default=None, help="Override recall threshold")
    args = parser.parse_args()

    pairs = resolve_pairs(args, parser)
    case_reports = []
    threshold = args.threshold

    for actual_path, gold_path in pairs:
        actual_blob = load_json(actual_path)
        gold_blob = load_json(gold_path)
        actual_cases = {case.get("case_id"): case for case in load_cases(actual_blob)}
        gold_cases = load_cases(gold_blob)

        if threshold is None and isinstance(gold_blob, dict) and "acceptance_threshold" in gold_blob:
            threshold = float(gold_blob["acceptance_threshold"])

        for gold_case in gold_cases:
            case_id = gold_case.get("case_id")
            actual_case = actual_cases.get(case_id)
            if actual_case is None:
                case_reports.append(
                    {
                        "case_id": case_id,
                        "layer": gold_case.get("layer"),
                        "is_positive": bool(gold_case.get("accepted_titles")),
                        "recall_hit": False,
                        "hit_at_1": False,
                        "hit_at_3": False,
                        "mrr": 0.0,
                        "preferred_hit_at_1": False,
                        "blocking_failures": ["MISSING_ACTUAL_CASE"],
                        "pass": False,
                    }
                )
                continue
            case_reports.append(score_case(actual_case, gold_case))

    if threshold is None:
        threshold = 0.85

    report = {
        "summary": aggregate(case_reports, threshold),
        "cases": case_reports,
        "method": "normalized-title-match",
        "inputs": [{"actual": str(actual), "gold": str(gold)} for actual, gold in pairs],
    }
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["summary"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
