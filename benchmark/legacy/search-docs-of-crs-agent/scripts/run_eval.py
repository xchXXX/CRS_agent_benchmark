#!/usr/bin/env python3
"""Run benchmark fixtures against a doc_search service and emit normalized actual outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request as urllib_request


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout_s: float) -> tuple[int, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
    with urllib_request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        body = resp.read().decode("utf-8")
        return status, json.loads(body)


def normalize_documents(track: str, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if track == "search_api":
        response_type = "documents" if body.get("results") else "message"
        results = body.get("results") or []
        docs = []
        for idx, item in enumerate(results, start=1):
            docs.append(
                {
                    "rank": idx,
                    "doc_title": str(item.get("title") or ""),
                    "doc_path": str(item.get("path") or ""),
                    "score": item.get("score"),
                }
            )
        return response_type, docs

    response_type = str(body.get("type") or "")
    content = body.get("content") or {}
    results = content.get("results") or []
    docs = []
    for idx, item in enumerate(results, start=1):
        docs.append(
            {
                "rank": idx,
                "doc_title": str(item.get("filename") or item.get("title") or ""),
                "doc_path": str(item.get("hierarchy_full") or item.get("path") or ""),
                "score": item.get("score"),
            }
        )
    return response_type, docs


def build_case_output(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "layer": case["layer"],
        "business_line": "DOC_SEARCH",
        "input_modality": case["input_modality"],
        "input": {
            "question_text": case.get("question_text", ""),
            "question_images": case.get("question_images", []),
            "vehicle_info": case.get("vehicle_info"),
        },
        "execution": {
            "run_id": None,
            "started_at": None,
            "ended_at": None,
            "duration_ms": None,
            "endpoint": None,
            "session_id": None,
        },
        "response": {
            "response_type": "",
            "final_status": "",
            "business": "DOC_SEARCH",
            "raw_summary": None,
        },
        "prediction": {
            "top_k_documents": [],
        },
        "workflow": {
            "planned_queries": [],
            "used_image_context": False,
            "ask_user_rounds": 0,
            "notes": case.get("notes"),
        },
        "validation": {
            "schema_pass": True,
            "blocking_failures": [],
            "warnings": [],
            "deterministic_hash": None,
        },
        "metrics": {
            "recall_hit": False,
            "hit_at_1": False,
            "hit_at_3": False,
            "mrr": 0.0,
        },
        "artifacts": {
            "raw_response_path": None,
            "normalized_output_path": None,
            "score_report_path": None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a benchmark fixture suite and emit normalized actual outputs.")
    parser.add_argument("fixture", help="Path to a fixture JSON containing a top-level cases array")
    parser.add_argument("--output", required=True, help="Where to write the normalized actual suite JSON")
    parser.add_argument("--base-url", default=os.environ.get("BENCHMARK_BASE_URL"), help="Base service URL")
    parser.add_argument("--app-token", default=os.environ.get("BENCHMARK_APP_TOKEN"), help="Optional app token")
    parser.add_argument("--timeout-ms", type=int, default=int(os.environ.get("BENCHMARK_TIMEOUT_MS", "30000")))
    parser.add_argument("--top-k", type=int, default=int(os.environ.get("BENCHMARK_TOP_K", "10")))
    args = parser.parse_args()

    if not args.base_url:
        raise SystemExit("BENCHMARK_BASE_URL or --base-url is required")

    fixture_blob = load_json(Path(args.fixture).resolve())
    if not isinstance(fixture_blob, dict) or not isinstance(fixture_blob.get("cases"), list):
        raise SystemExit("fixture must be a JSON object with a top-level cases array")

    suite_cases = fixture_blob["cases"]
    output_cases = []
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = {"Content-Type": "application/json"}
    if args.app_token:
        headers["x-app-token"] = args.app_token

    for case in suite_cases:
        actual = build_case_output(case)
        started = now_iso()
        actual["execution"]["started_at"] = started

        track = case.get("benchmark_track", "chat_completions")
        preprocess = case.get("preprocess_strategy", "none")
        request_context = case.get("request_context") or {}
        actual["workflow"]["used_image_context"] = bool(request_context)

        if preprocess != "none" and not request_context:
            actual["response"]["response_type"] = "skipped"
            actual["response"]["final_status"] = "skipped_preprocess_contract_missing"
            actual["validation"]["blocking_failures"].append("IMAGE_PREPROCESS_CONTRACT_NOT_CONFIGURED")
        else:
            if track == "search_api":
                url = args.base_url.rstrip("/") + "/search"
                payload = {
                    "query": case.get("question_text", ""),
                    "filters": {},
                    "limit": args.top_k,
                }
            else:
                url = args.base_url.rstrip("/") + "/chat/completions"
                payload = {
                    "message": case.get("question_text", ""),
                    "context": request_context,
                    "mode": "auto",
                }

            actual["execution"]["endpoint"] = url
            try:
                status, body = post_json(url, payload, headers, args.timeout_ms / 1000.0)
                response_type, docs = normalize_documents(track, body)
                actual["response"]["response_type"] = response_type
                actual["response"]["final_status"] = "success_documents" if docs else "success_message"
                actual["response"]["raw_summary"] = str(body.get("business") or body.get("status") or "")
                actual["prediction"]["top_k_documents"] = docs
                if status >= 400:
                    actual["validation"]["blocking_failures"].append(f"HTTP_{status}")
            except Exception as exc:
                actual["response"]["response_type"] = "error"
                actual["response"]["final_status"] = "error_http"
                actual["response"]["raw_summary"] = str(exc)
                actual["validation"]["blocking_failures"].append("HTTP_OR_RUNTIME_ERROR")

        actual["execution"]["ended_at"] = now_iso()
        hash_payload = json.dumps(
            {
                "response": actual["response"],
                "prediction": actual["prediction"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        actual["validation"]["deterministic_hash"] = hashlib.sha256(hash_payload).hexdigest()
        output_cases.append(actual)

    suite_output = {
        "benchmark_slug": "search-docs-of-crs-agent",
        "fixture_path": str(Path(args.fixture).resolve()),
        "generated_at": now_iso(),
        "cases": output_cases,
    }
    output_path.write_text(json.dumps(suite_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
