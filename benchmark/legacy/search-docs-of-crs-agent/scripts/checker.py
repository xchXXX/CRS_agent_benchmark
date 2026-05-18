#!/usr/bin/env python3
"""Validate normalized benchmark outputs against output.schema.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_type(name: str, value: object, expected: str | tuple[type, ...], errors: list[str]) -> None:
    if isinstance(expected, str):
        expected_name = expected
        ok = (
            (expected == "string" and isinstance(value, str))
            or (expected == "boolean" and isinstance(value, bool))
            or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (expected == "object" and isinstance(value, dict))
            or (expected == "array" and isinstance(value, list))
            or (expected == "null" and value is None)
        )
        if not ok:
            errors.append(f"{name}: expected {expected_name}")
        return

    if not isinstance(value, expected):
        errors.append(f"{name}: unexpected type")


def validate_document(name: str, item: dict[str, object], errors: list[str]) -> None:
    required = {"rank", "doc_title", "doc_path", "score"}
    missing = sorted(required - set(item))
    if missing:
        errors.append(f"{name}: missing keys {', '.join(missing)}")
    validate_type(f"{name}.rank", item.get("rank"), "integer", errors)
    validate_type(f"{name}.doc_title", item.get("doc_title"), "string", errors)
    validate_type(f"{name}.doc_path", item.get("doc_path"), "string", errors)
    score = item.get("score")
    if score is not None:
        validate_type(f"{name}.score", score, "number", errors)


def validate_case(case: dict[str, object], errors: list[str]) -> None:
    required = {
        "case_id",
        "layer",
        "business_line",
        "input_modality",
        "input",
        "response",
        "prediction",
        "validation",
        "metrics",
    }
    missing = sorted(required - set(case))
    if missing:
        errors.append(f"{case.get('case_id', '<unknown>')}: missing keys {', '.join(missing)}")
        return

    validate_type("case_id", case["case_id"], "string", errors)
    validate_type("layer", case["layer"], "string", errors)
    validate_type("business_line", case["business_line"], "string", errors)
    validate_type("input_modality", case["input_modality"], "string", errors)

    input_obj = case["input"]
    validate_type("input", input_obj, "object", errors)
    if isinstance(input_obj, dict):
        for key in ("question_text", "question_images", "vehicle_info"):
            if key not in input_obj:
                errors.append(f"input: missing key {key}")
        if "question_text" in input_obj:
            validate_type("input.question_text", input_obj["question_text"], "string", errors)
        if "question_images" in input_obj:
            validate_type("input.question_images", input_obj["question_images"], "array", errors)
        if isinstance(input_obj.get("question_images"), list):
            for idx, value in enumerate(input_obj["question_images"]):
                validate_type(f"input.question_images[{idx}]", value, "string", errors)

    response = case["response"]
    validate_type("response", response, "object", errors)
    if isinstance(response, dict):
        for key in ("response_type", "final_status", "business"):
            if key not in response:
                errors.append(f"response: missing key {key}")

    prediction = case["prediction"]
    validate_type("prediction", prediction, "object", errors)
    if isinstance(prediction, dict):
        docs = prediction.get("top_k_documents")
        validate_type("prediction.top_k_documents", docs, "array", errors)
        if isinstance(docs, list):
            for idx, item in enumerate(docs):
                validate_type(f"prediction.top_k_documents[{idx}]", item, "object", errors)
                if isinstance(item, dict):
                    validate_document(f"prediction.top_k_documents[{idx}]", item, errors)

    validation = case["validation"]
    validate_type("validation", validation, "object", errors)
    if isinstance(validation, dict):
        for key in ("schema_pass", "blocking_failures", "deterministic_hash"):
            if key not in validation:
                errors.append(f"validation: missing key {key}")

    metrics = case["metrics"]
    validate_type("metrics", metrics, "object", errors)
    if isinstance(metrics, dict):
        for key in ("recall_hit", "hit_at_1", "hit_at_3", "mrr"):
            if key not in metrics:
                errors.append(f"metrics: missing key {key}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a normalized benchmark output JSON.")
    parser.add_argument("actual", help="Path to a single-case output JSON or suite output JSON")
    parser.add_argument(
        "--schema",
        default=str(Path(__file__).resolve().parents[1] / "schema" / "output.schema.json"),
        help="Path to output.schema.json",
    )
    args = parser.parse_args()

    actual = load_json(Path(args.actual).resolve())
    schema = load_json(Path(args.schema).resolve())
    if not isinstance(schema, dict):
        print("[FAIL] schema must be a JSON object", file=sys.stderr)
        return 1

    errors: list[str] = []
    if isinstance(actual, dict) and isinstance(actual.get("cases"), list):
        for idx, case in enumerate(actual["cases"]):
            if not isinstance(case, dict):
                errors.append(f"cases[{idx}]: must be object")
                continue
            validate_case(case, errors)
    elif isinstance(actual, dict):
        validate_case(actual, errors)
    else:
        errors.append("actual artifact must be a JSON object")

    if errors:
        print("[FAIL] checker", file=sys.stderr)
        for item in errors:
            print(" - " + item, file=sys.stderr)
        return 1

    print("[PASS] checker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
