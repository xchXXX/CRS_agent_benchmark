from __future__ import annotations

import unicodedata


RECOMMEND_MARKERS = (
    "【推荐】",
    "[推荐]",
    "推荐:",
)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    for marker in RECOMMEND_MARKERS:
        normalized = normalized.replace(marker.lower(), "")
    chars: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if category.startswith("Z") or category.startswith("P") or category == "Cc":
            continue
        chars.append(char)
    return "".join(chars)
