"""Normalization helpers for the parameter-query domain."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.agent.domain.parameter_query.models import FIELD_ALIASES


QUESTION_FILLERS = (
    "请问",
    "帮我",
    "麻烦",
    "咨询一下",
    "问一下",
    "想问一下",
    "怎么看",
    "多少",
    "是多少",
    "什么",
)

PIN_PATTERN = re.compile(r"\b([A-Za-z]{1,4}\s*[-]?\s*\d{1,3})\b")
VOLTAGE_PATTERN = re.compile(r"(12|24)\s*[Vv伏]")
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[.\-_][A-Za-z0-9]+)*")
def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip().lower()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = normalized.replace("【", "[").replace("】", "]")
    normalized = re.sub(r"[\s`~!@#$%^&*()\-_=+\[\]{}\\|;:'\",.<>/?，。！？；：（）【】、]+", "", normalized)
    return normalized


FIELD_ALIAS_PATTERNS = {
    field: tuple(normalize_text(alias) for alias in aliases)
    for field, aliases in FIELD_ALIASES.items()
}


def normalize_pin_no(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return normalized or None


def extract_pin_token(value: str | None) -> str | None:
    if not value:
        return None

    candidates = [matched.group(0) for matched in TOKEN_PATTERN.finditer(value)]
    pin_like_candidates = [candidate for candidate in candidates if _looks_like_pin_token(candidate)]
    if not pin_like_candidates:
        return None

    # Prefer the last pin-like token because ECU model usually appears earlier,
    # while the actual pin token tends to be near "针脚/引脚/脚位" wording.
    return normalize_pin_no(pin_like_candidates[-1])


def _looks_like_pin_token(token: str) -> bool:
    stripped = token.strip()
    if not stripped:
        return False

    if any(separator in stripped for separator in ("-", ".", "_", " ")):
        compact = normalize_pin_no(stripped) or ""
        return 2 <= len(compact) <= 6

    letters = sum(char.isalpha() for char in stripped)
    digits = sum(char.isdigit() for char in stripped)
    if digits == 0:
        return False
    if letters <= 2 and len(stripped) <= 5:
        return True
    if letters == 0 and len(stripped) <= 4:
        return True
    return False


def detect_system_voltage(value: str | None) -> int | None:
    if not value:
        return None
    matched = VOLTAGE_PATTERN.search(value)
    if matched is None:
        return None
    return int(matched.group(1))


def extract_requested_field(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    for field, aliases in FIELD_ALIAS_PATTERNS.items():
        if any(alias and alias in normalized for alias in aliases):
            return field
    return None


def remove_known_terms(value: str, terms: list[str]) -> str:
    result = value
    for term in terms:
        if not term:
            continue
        result = result.replace(term, "")
    return result


def normalize_free_text_hint(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    for filler in QUESTION_FILLERS:
        normalized = normalized.replace(normalize_text(filler), "")
    return normalized or None


def bigrams(value: str) -> set[str]:
    if not value:
        return set()
    if len(value) < 2:
        return {value}
    return {value[index : index + 2] for index in range(len(value) - 1)}


def text_similarity(left: str | None, right: str | None) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    if normalized_left in normalized_right or normalized_right in normalized_left:
        shorter = min(len(normalized_left), len(normalized_right))
        longer = max(len(normalized_left), len(normalized_right))
        return min(0.98, 0.72 + shorter / max(longer, 1) * 0.22)
    left_bigrams = bigrams(normalized_left)
    right_bigrams = bigrams(normalized_right)
    if left_bigrams and right_bigrams:
        overlap = len(left_bigrams & right_bigrams) / max(len(left_bigrams | right_bigrams), 1)
    else:
        overlap = 0.0
    ratio = SequenceMatcher(a=normalized_left, b=normalized_right).ratio()
    return max(overlap, ratio * 0.9)


def first_number_pair(value: str | None) -> tuple[float | None, float | None]:
    if not value:
        return None, None
    numbers = [float(item) for item in NUMBER_PATTERN.findall(value)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
