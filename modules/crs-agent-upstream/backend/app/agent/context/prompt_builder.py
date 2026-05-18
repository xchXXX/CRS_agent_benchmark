"""Prompt summaries for shared working state and case context."""

from __future__ import annotations

from app.agent.context.models import CaseContext


class CaseContextPromptBuilder:
    """Build a compact, bounded working-state summary for the agent prompt."""

    HEADER = "[CASE_CONTEXT]"
    FOOTER_NOTE = "如本轮问题与以上证据无关，以本轮用户问题为准。"
    FOOTER = "[/CASE_CONTEXT]"

    SLOT_LABELS = {
        "brand": "品牌",
        "series": "车系",
        "model": "车型",
        "platform": "平台",
        "engine": "发动机",
        "emission": "排放阶段",
        "doc_type": "资料类型",
        "fault_code": "故障码",
        "symptom": "故障现象",
        "subsystem": "子系统",
        "ecu_model": "ECU",
        "parameter_source_id": "参数资料源",
        "supplemental_information": "补充信息",
    }

    def __init__(
        self,
        *,
        max_slots: int = 10,
        max_artifacts: int = 6,
        max_missing_slots: int = 4,
        max_actions: int = 4,
        max_chars: int = 1800,
    ) -> None:
        self._max_slots = max_slots
        self._max_artifacts = max_artifacts
        self._max_missing_slots = max_missing_slots
        self._max_actions = max_actions
        self._max_chars = max_chars

    def build(self, context: CaseContext | None) -> str:
        if context is None:
            return ""

        working_state_lines = self._build_working_state_lines(context)
        slot_lines = self._build_slot_lines(context)
        missing_lines = self._build_missing_lines(context)
        action_lines = self._build_action_lines(context)
        candidate_lines = self._build_candidate_lines(context)
        artifact_lines = self._build_artifact_lines(context)
        pending_lines = self._build_pending_lines(context)
        if not working_state_lines and not slot_lines and not missing_lines and not action_lines and not candidate_lines and not artifact_lines and not pending_lines:
            return ""

        prompt = self._assemble_prompt(
            working_state_lines=working_state_lines,
            slot_lines=slot_lines,
            missing_lines=missing_lines,
            action_lines=action_lines,
            candidate_lines=candidate_lines,
            artifact_lines=artifact_lines,
            pending_lines=pending_lines,
        )
        if len(prompt) <= self._max_chars:
            return prompt

        trimmed_artifact_lines = list(artifact_lines)
        trimmed_action_lines = list(action_lines)
        while len(prompt) > self._max_chars and (trimmed_artifact_lines or trimmed_action_lines):
            if trimmed_artifact_lines:
                trimmed_artifact_lines.pop()
            elif trimmed_action_lines:
                trimmed_action_lines.pop()
            prompt = self._assemble_prompt(
                working_state_lines=working_state_lines,
                slot_lines=slot_lines,
                missing_lines=missing_lines,
                action_lines=trimmed_action_lines,
                candidate_lines=candidate_lines,
                artifact_lines=trimmed_artifact_lines,
                pending_lines=pending_lines,
            )

        return self._assemble_bounded_prompt(
            working_state_lines=working_state_lines,
            slot_lines=slot_lines,
            missing_lines=missing_lines,
            action_lines=trimmed_action_lines,
            candidate_lines=candidate_lines,
            artifact_lines=trimmed_artifact_lines,
            pending_lines=pending_lines,
        )

    def _assemble_prompt(
        self,
        *,
        working_state_lines: list[str],
        slot_lines: list[str],
        missing_lines: list[str],
        action_lines: list[str],
        candidate_lines: list[str],
        artifact_lines: list[str],
        pending_lines: list[str],
    ) -> str:
        parts = [self.HEADER]
        if working_state_lines:
            parts.append("工作状态:")
            parts.extend(working_state_lines)
        if slot_lines:
            parts.append("已确认信息:")
            parts.extend(slot_lines)
        if missing_lines:
            parts.append("缺失信息:")
            parts.extend(missing_lines)
        if action_lines:
            parts.append("最近动作:")
            parts.extend(action_lines)
        if candidate_lines:
            parts.append("候选结论:")
            parts.extend(candidate_lines)
        if artifact_lines:
            parts.append("最近证据:")
            parts.extend(artifact_lines)
        if pending_lines:
            parts.append("当前待确认:")
            parts.extend(pending_lines)
        parts.append(self.FOOTER_NOTE)
        parts.append(self.FOOTER)
        return "\n".join(parts)

    def _assemble_bounded_prompt(
        self,
        *,
        working_state_lines: list[str],
        slot_lines: list[str],
        missing_lines: list[str],
        action_lines: list[str],
        candidate_lines: list[str],
        artifact_lines: list[str],
        pending_lines: list[str],
    ) -> str:
        parts = [self.HEADER]
        footer_lines = [self.FOOTER_NOTE, self.FOOTER]
        sections = [
            ("工作状态:", working_state_lines),
            ("已确认信息:", slot_lines),
            ("缺失信息:", missing_lines),
            ("最近动作:", action_lines),
            ("候选结论:", candidate_lines),
            ("最近证据:", artifact_lines),
            ("当前待确认:", pending_lines),
        ]
        for title, lines in sections:
            if not lines:
                continue
            if not self._try_append_line(parts, title, reserved_lines=footer_lines):
                break
            for line in lines:
                if not self._try_append_line(parts, line, reserved_lines=footer_lines):
                    break

        self._try_append_line(parts, self.FOOTER_NOTE, reserved_lines=[self.FOOTER])
        if not self._try_append_line(parts, self.FOOTER):
            minimal = "\n".join([self.HEADER, self.FOOTER])
            if len(minimal) <= self._max_chars:
                return minimal
            return self._truncate_line(self.HEADER, self._max_chars)
        prompt = "\n".join(parts)
        if len(prompt) <= self._max_chars:
            return prompt
        return self._truncate_line(prompt, self._max_chars)

    def _try_append_line(
        self,
        parts: list[str],
        line: str,
        *,
        reserved_lines: list[str] | None = None,
    ) -> bool:
        reserved = reserved_lines or []
        candidate = parts + [line] + reserved
        if len("\n".join(candidate)) <= self._max_chars:
            parts.append(line)
            return True

        remaining = self._max_chars - len("\n".join(parts + reserved)) - 1
        truncated = self._truncate_line(line, remaining)
        if truncated:
            parts.append(truncated)
            return True
        return False

    @staticmethod
    def _truncate_line(line: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if len(line) <= max_chars:
            return line
        if max_chars <= 3:
            return line[:max_chars]
        return f"{line[: max_chars - 3].rstrip()}..."

    def _build_slot_lines(self, context: CaseContext) -> list[str]:
        lines: list[str] = []
        slots = context.slots.model_dump(mode="json")
        for key in [
            "brand",
            "series",
            "model",
            "platform",
            "engine",
            "emission",
            "doc_type",
            "fault_code",
            "symptom",
            "subsystem",
            "ecu_model",
            "parameter_source_id",
        ]:
            value = slots.get(key)
            if value in (None, "", []):
                continue
            lines.append(f"- {self.SLOT_LABELS[key]}: {value}")
            if len(lines) >= self._max_slots:
                break

        if context.slots.selected_doc_titles:
            lines.append(f"- 已选资料: {', '.join(context.slots.selected_doc_titles[:3])}")
        return lines[: self._max_slots]

    def _build_working_state_lines(self, context: CaseContext) -> list[str]:
        lines: list[str] = []
        has_state = bool(
            context.task_type
            or context.missing_slots
            or context.attempted_actions
            or context.candidate_answer is not None
            or context.no_gain_streak > 0
            or context.answer_ready
        )
        if not has_state:
            return lines

        if context.task_type:
            lines.append(f"- 当前任务: {context.task_type}")
        lines.append(f"- 回答阈值: {'已满足' if context.answer_ready else '未满足'}")
        if context.no_gain_streak > 0:
            lines.append(f"- 连续无增量: {context.no_gain_streak}")

        budget_parts: list[str] = []
        if context.remaining_budget.tool_calls_left is not None:
            budget_parts.append(f"tool {context.remaining_budget.tool_calls_left}")
        if context.remaining_budget.external_calls_left is not None:
            budget_parts.append(f"external {context.remaining_budget.external_calls_left}")
        if context.remaining_budget.ask_user_calls_left is not None:
            budget_parts.append(f"ask_user {context.remaining_budget.ask_user_calls_left}")
        if budget_parts:
            lines.append(f"- 剩余预算: {' / '.join(budget_parts)}")
        return lines

    def _build_missing_lines(self, context: CaseContext) -> list[str]:
        lines: list[str] = []
        for slot_name in context.missing_slots[: self._max_missing_slots]:
            label = self.SLOT_LABELS.get(slot_name, slot_name)
            lines.append(f"- {label}")
        return lines

    def _build_action_lines(self, context: CaseContext) -> list[str]:
        lines: list[str] = []
        for action in reversed(context.attempted_actions[-self._max_actions :]):
            parts = [action.action]
            if action.info_gain:
                parts.append(f"gain={action.info_gain}")
            if action.filled_slots:
                filled = ", ".join(self.SLOT_LABELS.get(slot, slot) for slot in action.filled_slots[:3])
                parts.append(f"补齐={filled}")
            line = " | ".join(parts) + f" | {action.result_summary}"
            lines.append(f"- {line}")
        return lines

    @staticmethod
    def _build_candidate_lines(context: CaseContext) -> list[str]:
        if context.candidate_answer is None:
            return []
        return [f"- {context.candidate_answer.business}: {context.candidate_answer.summary}"]

    def _build_artifact_lines(self, context: CaseContext) -> list[str]:
        artifacts = list(context.artifacts[-self._max_artifacts :])
        lines: list[str] = []
        for artifact in reversed(artifacts):
            summary = artifact.summary.strip()
            if not summary:
                continue
            lines.append(f"- {summary}")
        return lines[: self._max_artifacts]

    @staticmethod
    def _build_pending_lines(context: CaseContext) -> list[str]:
        pending = context.pending_action
        if pending is None:
            return []
        options = f"（可选：{' / '.join(pending.options_summary[:3])}）" if pending.options_summary else ""
        return [f"- {pending.question}{options}"]
