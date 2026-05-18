from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PreprocessOutcome:
    request_context: dict[str, Any]
    used_image_context: bool
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def prepare_request_context(task) -> PreprocessOutcome:
    request_context = task.request_context if isinstance(task.request_context, dict) else {}
    if task.preprocess_strategy == "none":
        return PreprocessOutcome(
            request_context=request_context,
            used_image_context=bool(request_context) or bool(getattr(task, "question_images", [])),
        )

    if getattr(task, "question_images", []):
        return PreprocessOutcome(
            request_context=request_context,
            used_image_context=True,
        )

    if not request_context:
        return PreprocessOutcome(
            request_context={},
            used_image_context=False,
            blocking_failures=["OCR_CONTEXT_MISSING"],
        )

    return PreprocessOutcome(
        request_context=request_context,
        used_image_context=True,
    )
