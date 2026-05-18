"""Loop safety guards."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any


class LoopGuardExceededError(RuntimeError):
    """Raised when a single agent run exceeds configured tool-call budgets."""

    def __init__(self, message: str, *, tool_name: str, error_code: str) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.error_code = error_code


@dataclass(frozen=True)
class LoopGuardBudgetSnapshot:
    total_tool_calls: int
    max_tool_calls: int
    remaining_tool_calls: int
    external_tool_calls: int
    max_external_tool_calls: int | None
    remaining_external_tool_calls: int | None
    ask_user_calls: int
    max_ask_user_calls: int | None
    remaining_ask_user_calls: int | None
    no_gain_streak: int
    max_no_gain_streak: int | None
    tool_counts: dict[str, int]
    last_tool_name: str | None
    last_tool_category: str | None
    last_info_gain: str | None


@dataclass
class LoopGuard:
    max_tool_calls: int
    max_same_tool_repeat: int
    max_same_args_repeat: int
    max_external_tool_calls: int | None = None
    max_ask_user_calls: int | None = None
    max_no_gain_streak: int | None = None
    total_tool_calls: int = 0
    external_tool_calls: int = 0
    ask_user_calls: int = 0
    no_gain_streak: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    args_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    last_tool_name: str | None = None
    last_tool_category: str | None = None
    last_info_gain: str | None = None

    def before_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        is_external: bool = False,
        is_user_interaction: bool = False,
    ) -> None:
        self.last_tool_name = tool_name
        self.last_tool_category = self._resolve_category(
            is_external=is_external,
            is_user_interaction=is_user_interaction,
        )
        self.total_tool_calls += 1
        if self.total_tool_calls > self.max_tool_calls:
            raise LoopGuardExceededError(
                f"Tool call budget exceeded after {self.total_tool_calls} calls.",
                tool_name=tool_name,
                error_code="LOOP_GUARD_MAX_TOOL_CALLS",
            )

        if is_external:
            self.external_tool_calls += 1
            if (
                self.max_external_tool_calls is not None
                and self.external_tool_calls > self.max_external_tool_calls
            ):
                raise LoopGuardExceededError(
                    f"External tool budget exceeded after {self.external_tool_calls} calls.",
                    tool_name=tool_name,
                    error_code="LOOP_GUARD_MAX_EXTERNAL_TOOL_CALLS",
                )

        if is_user_interaction:
            self.ask_user_calls += 1
            if (
                self.max_ask_user_calls is not None
                and self.ask_user_calls > self.max_ask_user_calls
            ):
                raise LoopGuardExceededError(
                    f"`ask_user_question` was called too many times in one run.",
                    tool_name=tool_name,
                    error_code="LOOP_GUARD_MAX_ASK_USER_CALLS",
                )

        tool_count = self.tool_counts.get(tool_name, 0) + 1
        self.tool_counts[tool_name] = tool_count
        if tool_count > self.max_same_tool_repeat:
            raise LoopGuardExceededError(
                f"Tool `{tool_name}` was called too many times in one run.",
                tool_name=tool_name,
                error_code="LOOP_GUARD_MAX_SAME_TOOL_REPEAT",
            )

        signature = self._hash_args(args or {})
        count_key = (tool_name, signature)
        args_count = self.args_counts.get(count_key, 0) + 1
        self.args_counts[count_key] = args_count
        if args_count > self.max_same_args_repeat:
            raise LoopGuardExceededError(
                f"Tool `{tool_name}` repeated the same arguments too many times.",
                tool_name=tool_name,
                error_code="LOOP_GUARD_MAX_SAME_ARGS_REPEAT",
            )

    def after_tool_call(self, tool_name: str, result: dict[str, Any] | Any) -> str:
        info_gain = self._infer_info_gain(result)
        self.last_tool_name = tool_name
        self.last_info_gain = info_gain
        if info_gain == "none":
            self.no_gain_streak += 1
            if (
                self.max_no_gain_streak is not None
                and self.no_gain_streak > self.max_no_gain_streak
            ):
                raise LoopGuardExceededError(
                    f"No-information tool streak exceeded after {self.no_gain_streak} consecutive low-value calls.",
                    tool_name=tool_name,
                    error_code="LOOP_GUARD_MAX_NO_GAIN_STREAK",
                )
        else:
            self.no_gain_streak = 0
        return info_gain

    def snapshot(self) -> LoopGuardBudgetSnapshot:
        return LoopGuardBudgetSnapshot(
            total_tool_calls=self.total_tool_calls,
            max_tool_calls=self.max_tool_calls,
            remaining_tool_calls=max(self.max_tool_calls - self.total_tool_calls, 0),
            external_tool_calls=self.external_tool_calls,
            max_external_tool_calls=self.max_external_tool_calls,
            remaining_external_tool_calls=self._remaining_budget(
                self.max_external_tool_calls,
                self.external_tool_calls,
            ),
            ask_user_calls=self.ask_user_calls,
            max_ask_user_calls=self.max_ask_user_calls,
            remaining_ask_user_calls=self._remaining_budget(
                self.max_ask_user_calls,
                self.ask_user_calls,
            ),
            no_gain_streak=self.no_gain_streak,
            max_no_gain_streak=self.max_no_gain_streak,
            tool_counts=dict(self.tool_counts),
            last_tool_name=self.last_tool_name,
            last_tool_category=self.last_tool_category,
            last_info_gain=self.last_info_gain,
        )

    @staticmethod
    def _hash_args(args: dict[str, Any]) -> str:
        normalized = LoopGuard._normalize_value(args)
        serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
        return sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): cls._normalize_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, list):
            normalized_items = [cls._normalize_value(item) for item in value]
            return sorted(normalized_items, key=cls._stable_sort_key)
        if isinstance(value, tuple):
            normalized_items = [cls._normalize_value(item) for item in value]
            return sorted(normalized_items, key=cls._stable_sort_key)
        if isinstance(value, str):
            return re.sub(r"\s+", " ", value).strip()
        return value

    @staticmethod
    def _stable_sort_key(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _resolve_category(*, is_external: bool, is_user_interaction: bool) -> str:
        if is_user_interaction:
            return "interaction"
        if is_external:
            return "external"
        return "local"

    @classmethod
    def _infer_info_gain(cls, result: dict[str, Any] | Any) -> str:
        if not result:
            return "none"

        if not isinstance(result, dict):
            return "medium"

        status = str(result.get("status") or "").strip().lower()
        if status == "failed":
            return "none"
        if status in {"need_clarify", "deferred"}:
            return "medium"

        if result.get("success") is False:
            return "none"

        data = result.get("data")
        if isinstance(data, dict):
            if data.get("matched") is False and not data.get("rows"):
                return "none"
            if data.get("loaded") is False and not data.get("entries"):
                return "none"
            if (
                ("count" in data or "candidates" in data)
                and int(data.get("count") or 0) <= 0
                and isinstance(data.get("candidates"), list)
                and not data.get("candidates")
            ):
                return "none"
            if (
                ("title_count" in data or "recommended_titles" in data or "titles" in data)
                and int(data.get("title_count") or 0) <= 0
                and not data.get("recommended_titles")
                and not data.get("titles")
            ):
                return "none"
            if (
                ("returned_count" in data or "results" in data)
                and int(data.get("returned_count") or 0) <= 0
                and not data.get("results")
            ):
                return "none"

        if cls._has_meaningful_payload(result):
            return "medium"
        return "none"

    @classmethod
    def _has_meaningful_payload(cls, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            stripped = value.strip()
            return bool(stripped) and stripped.lower() not in {"ok", "none", "null", "n/a"}
        if isinstance(value, list):
            return any(cls._has_meaningful_payload(item) for item in value)
        if isinstance(value, dict):
            ignore_keys = {
                "status",
                "query",
                "message",
                "guidance",
                "decision_mode",
                "source",
                "source_refs",
            }
            for key, item in value.items():
                if key in ignore_keys:
                    continue
                if cls._has_meaningful_payload(item):
                    return True
            return False
        return True

    @staticmethod
    def _remaining_budget(limit: int | None, used: int) -> int | None:
        if limit is None:
            return None
        return max(limit - used, 0)
