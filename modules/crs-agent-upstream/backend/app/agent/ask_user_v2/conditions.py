"""Condition evaluation helpers for Ask User v2."""

from __future__ import annotations

from typing import Any

from app.agent.ask_user_v2.schema import AskUserFormCondition


def normalize_answer_entry(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        raw_selected = value.get("selected")
        if isinstance(raw_selected, list):
            selected = [str(item).strip() for item in raw_selected if str(item).strip()]
        elif raw_selected in (None, ""):
            selected = []
        else:
            selected = [str(raw_selected).strip()]
        text = str(value.get("text") or "").strip()
        return {"selected": selected, "text": text}

    if isinstance(value, list):
        return {"selected": [str(item).strip() for item in value if str(item).strip()], "text": ""}

    text = str(value or "").strip()
    return {"selected": [], "text": text}


def is_answered(value: Any) -> bool:
    entry = normalize_answer_entry(value)
    return bool(entry["selected"] or entry["text"])


def evaluate_condition(condition: AskUserFormCondition, answers: dict[str, Any]) -> bool:
    entry = normalize_answer_entry(answers.get(condition.field))
    selected = entry["selected"]
    text = entry["text"]
    target = condition.value

    if condition.op == "equals":
        return text == str(target or "") or str(target or "") in selected
    if condition.op == "not_equals":
        return text != str(target or "") and str(target or "") not in selected
    if condition.op == "includes":
        return str(target or "") in selected
    if condition.op == "not_includes":
        return str(target or "") not in selected
    if condition.op == "is_truthy":
        return bool(selected or text)
    if condition.op == "is_filled":
        return bool(selected or text)
    if condition.op == "is_empty":
        return not (selected or text)
    return False


def evaluate_conditions(conditions: list[AskUserFormCondition], answers: dict[str, Any]) -> bool:
    if not conditions:
        return True
    return all(evaluate_condition(condition, answers) for condition in conditions)
