"""Runtime adapters for parameter-query tool outputs."""

from __future__ import annotations

from uuid import uuid4

from app.agent.ask_user_v2 import (
    attach_form_to_ask_user,
    build_single_field_form,
    extract_form_answer_fields,
    extract_primary_answer_text,
)
from app.agent.memory.deferred_store import DeferredState
from app.agent.models.ask_user import AskUserInputType, AskUserOption, AskUserQuestion
from app.schemas.chat import AskUserAnswer


PARAM_QUERY_DEFERRED_TOOL_NAME = "parameter_query_clarify"


class ParameterQueryResponseAdapter:
    """Translate parameter-query tool payloads into frontend-facing responses."""

    @staticmethod
    def build_ask_user_question(
        envelope: dict,
        *,
        tool_call_id: str | None = None,
    ) -> AskUserQuestion:
        clarify = envelope.get("clarify") or {}
        context = clarify.get("context") or {}
        clarify_type = str(context.get("clarify_type") or "").strip().lower()
        options = [AskUserOption.model_validate(item) for item in (clarify.get("options") or [])]
        has_options = bool(options)
        input_type = AskUserInputType.SINGLE_SELECT if has_options else AskUserInputType.TEXT
        pin_examples = [
            str(item).strip()
            for item in (context.get("pin_examples") or [])
            if str(item).strip()
        ][:3]
        pin_example_text = f"例如：{'、'.join(pin_examples)}。" if pin_examples else ""
        if clarify_type == "row":
            default_question = "请确认要查的针脚" if has_options else "请补充更准确的针脚编号、信号名称或部件名称"
            default_input_hint = (
                "也可以直接输入更准确的针脚编号或信号名称"
                if has_options
                else "也可以直接输入更准确的针脚编号、信号名称或部件名称"
            )
            form_description = (
                "已定位到 ECU，优先点选最接近的针脚或部件；没有合适项再自行补充。"
                if has_options
                else f"已定位到 ECU，请直接补充更准确的针脚编号、信号名称或部件名称。{pin_example_text}"
            )
            ask_reason = str(context.get("message") or "").strip() or (
                "当前还需要确认具体针脚或目标行。"
                if has_options
                else f"当前还需要补充更准确的针脚或目标对象。{pin_example_text}"
            )
            if pin_example_text and not has_options and pin_example_text not in ask_reason:
                ask_reason = f"{ask_reason}{pin_example_text}"
        else:
            default_question = "请先确认 ECU 型号" if has_options else "请补充 ECU / 控制器型号"
            default_input_hint = (
                "也可以直接输入 ECU 型号"
                if has_options
                else "也可以直接输入 ECU / 控制器型号，或补充车型与发动机信息"
            )
            form_description = (
                "优先点选候选 ECU，没有合适项再自行补充。"
                if has_options
                else "请直接补充 ECU / 控制器型号；如果暂时不清楚，也可以补充车型、发动机或系统信息。"
            )
            ask_reason = context.get("message") or (
                "当前还需要确认 ECU 或资料来源。"
                if has_options
                else "当前还无法从本地资料中唯一定位 ECU 或资料来源。"
            )

        raw_question = str(clarify.get("question") or "").strip()
        if not has_options and clarify_type != "row":
            question = default_question
        else:
            question = raw_question or default_question
        input_hint = (context.get("input_hint") or default_input_hint).strip() or default_input_hint
        ask_user = AskUserQuestion(
            tool_call_id=tool_call_id or f"parameter_query_{uuid4().hex}",
            question=question,
            input_type=input_type,
            options=options,
            allow_free_input=True,
            input_hint=input_hint,
            context=context,
        )
        form = build_single_field_form(
            form_id=f"parameter_query_form_{ask_user.tool_call_id}",
            title="参数查询补充",
            description=form_description,
            ask_reason=ask_reason,
            field_key=str(context.get("clarify_type") or "parameter_source"),
            field_label=question,
            input_type=input_type,
            options=options,
            allow_free_input=True,
            input_hint=input_hint,
            auto_submit_single_select=True,
            manual_input_always_visible=True,
        )
        form.ui_policy.show_summary_preview = False
        form.ui_policy.dense = True
        return attach_form_to_ask_user(
            ask_user,
            form=form,
            scene="parameter_query",
            extra_context={"clarify_type": context.get("clarify_type") or "parameter_source"},
        )

    @staticmethod
    def build_deferred_state(
        *,
        tool_call_id: str,
        message_history_json: str,
        query: str,
        ask_user: AskUserQuestion,
    ) -> DeferredState:
        return DeferredState(
            tool_call_id=tool_call_id,
            tool_name=PARAM_QUERY_DEFERRED_TOOL_NAME,
            message_history_json=message_history_json,
            payload={
                "query": query,
                "ask_user": ask_user.model_dump(mode="json"),
            },
        )

    @staticmethod
    def resolve_selection_payload(answer: AskUserAnswer, deferred_state: DeferredState) -> dict | None:
        metadata_payload = answer.metadata.get("selection_payload") if answer.metadata else None
        if isinstance(metadata_payload, dict):
            return metadata_payload

        ask_user_payload = deferred_state.payload.get("ask_user") or {}
        fields = extract_form_answer_fields(answer.answer)
        if fields:
            for item in fields.values():
                for selected in item.get("selected") or []:
                    for option in ask_user_payload.get("options", []):
                        if selected in {str(option.get("label", "")), str(option.get("key", ""))}:
                            selection_payload = option.get("selection_payload")
                            if isinstance(selection_payload, dict):
                                return selection_payload

        answer_text = extract_primary_answer_text(answer.answer)
        if not answer_text:
            return None

        for option in ask_user_payload.get("options", []):
            if answer_text in {str(option.get("label", "")), str(option.get("key", ""))}:
                selection_payload = option.get("selection_payload")
                if isinstance(selection_payload, dict):
                    return selection_payload
        return None

    @staticmethod
    def resolve_query_hint(answer: AskUserAnswer) -> str:
        fields = extract_form_answer_fields(answer.answer)
        for item in fields.values():
            text = str(item.get("text") or "").strip()
            if text:
                return text
        return extract_primary_answer_text(answer.answer)

    @staticmethod
    def build_param_request_content(data: dict) -> dict:
        return {
            "query": data.get("query") or "",
            "summary": data.get("summary") or "",
            "requested_field": data.get("requested_field"),
            "requested_field_label": data.get("requested_field_label"),
            "selected_source": data.get("selected_source") or {},
            "rows": data.get("rows") or [],
            "source_refs": data.get("source_refs") or [],
        }
