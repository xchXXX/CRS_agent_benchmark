"""Normalization helpers for Ask User v2."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.agent.ask_user_v2.schema import (
    AskUserForm,
    AskUserFormField,
    AskUserFormManualInput,
    AskUserFormOption,
    AskUserFormSection,
    extract_ask_user_form,
)
from app.agent.ask_user_v2.smart_option_enricher import smart_ask_user_option_enricher, to_form_options
from app.agent.ask_user_v2.validator import validate_form
from app.agent.models.ask_user import AskUserInputType, AskUserQuestion


def _build_form_options(options: list[Any]) -> list[AskUserFormOption]:
    normalized: list[AskUserFormOption] = []
    for option in options:
        if hasattr(option, "model_dump"):
            payload = option.model_dump(mode="json")
        elif isinstance(option, dict):
            payload = dict(option)
        else:
            continue
        normalized.append(
            AskUserFormOption(
                key=str(payload.get("key") or "").strip(),
                label=str(payload.get("label") or payload.get("key") or "").strip(),
                description=str(payload.get("description") or "").strip() or None,
                option_source=str(payload.get("option_source") or "system"),
                evidence_level=str(payload.get("evidence_level") or "confirmed"),
                selection_payload=payload.get("selection_payload") or {},
                tags=list(payload.get("tags") or []),
            )
        )
    return [item for item in normalized if item.key and item.label]


def build_single_field_form(
    *,
    form_id: str | None = None,
    title: str,
    description: str | None = None,
    ask_reason: str | None = None,
    field_key: str,
    field_label: str,
    input_type: AskUserInputType,
    options: list[Any] | None = None,
    allow_free_input: bool = False,
    input_hint: str | None = None,
    auto_submit_single_select: bool = False,
    manual_input_always_visible: bool = False,
) -> AskUserForm:
    form_options = _build_form_options(list(options or []))

    if input_type == AskUserInputType.SINGLE_SELECT:
        field_type = "single_select"
        answer_mode = "select_or_text" if allow_free_input else "select_only"
    elif input_type == AskUserInputType.MULTI_SELECT:
        field_type = "multi_select"
        answer_mode = "select_and_text" if allow_free_input else "select_only"
    elif input_type == AskUserInputType.NUMBER:
        field_type = "number"
        answer_mode = "number_only"
    else:
        field_type = "text"
        answer_mode = "text_only"

    manual_input = None
    if allow_free_input and input_type in {AskUserInputType.SINGLE_SELECT, AskUserInputType.MULTI_SELECT, AskUserInputType.TEXT}:
        manual_input = AskUserFormManualInput(
            enabled=True,
            always_visible=manual_input_always_visible,
            placeholder=input_hint or f"补充{field_label}",
            input_hint=input_hint,
            value_type="text",
        )

    field = AskUserFormField(
        key=field_key,
        label=field_label,
        field_type=field_type,
        answer_mode=answer_mode,
        required=True,
        required_level="hard",
        placeholder=input_hint,
        hint=input_hint if input_type != AskUserInputType.TEXT else None,
        options=form_options,
        manual_input=manual_input,
        submit_on_select=auto_submit_single_select and input_type == AskUserInputType.SINGLE_SELECT and not allow_free_input,
    )

    form = AskUserForm(
        form_id=form_id or f"ask_form_{uuid4().hex}",
        title=title,
        description=description,
        ask_reason=ask_reason,
        mode="single_page",
        sections=[
            AskUserFormSection(
                id="core",
                title=title,
                description=description,
                fields=[field],
            )
        ],
    )
    if auto_submit_single_select and input_type == AskUserInputType.SINGLE_SELECT and not allow_free_input:
        form.ui_policy.auto_submit_single_select = True
    return validate_form(form)


def attach_form_to_ask_user(
    ask_user: AskUserQuestion,
    *,
    form: AskUserForm,
    card_type: str = "ask_form_v2",
    scene: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> AskUserQuestion:
    validated_form = validate_form(form)
    context = dict(ask_user.context or {})
    if extra_context:
        context.update(extra_context)
    context["schema_version"] = "2.0"
    context["card_type"] = card_type
    if scene:
        context["scene"] = scene
    context["form"] = validated_form.model_dump(mode="json")
    return ask_user.model_copy(update={"context": context})


def normalize_ask_user_question_v2(ask_user: AskUserQuestion) -> AskUserQuestion:
    context = dict(ask_user.context or {})
    form = extract_ask_user_form(context)
    if form is None:
        suggestion = smart_ask_user_option_enricher.maybe_build_field_suggestion(ask_user=ask_user)
        if suggestion is None or not suggestion.options:
            return ask_user

        form = build_single_field_form(
            form_id=f"ask_form_{ask_user.tool_call_id}",
            title=suggestion.title or "请先补充关键信息",
            description=None,
            ask_reason=str(context.get("ask_reason") or "").strip() or None,
            field_key="predicted_model_or_system",
            field_label=suggestion.field_label or "型号或系统信息",
            input_type=AskUserInputType.SINGLE_SELECT,
            options=[option.model_dump(mode="json") for option in to_form_options(suggestion.options)],
            allow_free_input=True,
            input_hint=suggestion.input_hint,
            auto_submit_single_select=False,
            manual_input_always_visible=False,
        )
        return attach_form_to_ask_user(
            ask_user,
            form=form,
            card_type="ask_form_v2",
            scene=str(context.get("scene") or "").strip() or None,
            extra_context={
                "smart_options_generated": True,
                "smart_options_field": "predicted_model_or_system",
            },
        )

    fields = list(form.iter_fields())
    if len(fields) == 1 and not (fields[0].options or []):
        suggestion = smart_ask_user_option_enricher.maybe_build_field_suggestion(ask_user=ask_user)
        if suggestion is not None and suggestion.options:
            field = fields[0]
            rebuilt_form = build_single_field_form(
                form_id=form.form_id,
                title=form.title,
                description=form.description,
                ask_reason=form.ask_reason,
                field_key=field.key,
                field_label=field.label or suggestion.field_label or "请补充必要信息",
                input_type=AskUserInputType.SINGLE_SELECT,
                options=[option.model_dump(mode="json") for option in to_form_options(suggestion.options)],
                allow_free_input=True,
                input_hint=suggestion.input_hint or field.placeholder or field.hint,
                auto_submit_single_select=bool(form.ui_policy.auto_submit_single_select),
                manual_input_always_visible=bool(field.manual_input and field.manual_input.always_visible),
            )
            rebuilt_form.ui_policy.show_summary_preview = form.ui_policy.show_summary_preview
            rebuilt_form.ui_policy.dense = form.ui_policy.dense
            rebuilt_form.ui_policy.submit_button_text = form.ui_policy.submit_button_text
            rebuilt_form.ui_policy.layout = form.ui_policy.layout
            return attach_form_to_ask_user(
                ask_user,
                form=rebuilt_form,
                card_type=str(context.get("card_type") or "ask_form_v2"),
                scene=str(context.get("scene") or "").strip() or None,
                extra_context={
                    "smart_options_generated": True,
                    "smart_options_field": field.key,
                },
            )

    context["schema_version"] = "2.0"
    context.setdefault("card_type", "ask_form_v2")
    context["form"] = validate_form(form).model_dump(mode="json")
    return ask_user.model_copy(update={"context": context})


async def normalize_ask_user_question_v2_async(ask_user: AskUserQuestion) -> AskUserQuestion:
    context = dict(ask_user.context or {})
    form = extract_ask_user_form(context)
    if form is None:
        suggestion = await smart_ask_user_option_enricher.maybe_build_field_suggestion_async(ask_user=ask_user)
        if suggestion is None or not suggestion.options:
            return ask_user

        form = build_single_field_form(
            form_id=f"ask_form_{ask_user.tool_call_id}",
            title=suggestion.title or "请先补充关键信息",
            description=None,
            ask_reason=str(context.get("ask_reason") or "").strip() or None,
            field_key="predicted_model_or_system",
            field_label=suggestion.field_label or "型号或系统信息",
            input_type=AskUserInputType.SINGLE_SELECT,
            options=[option.model_dump(mode="json") for option in to_form_options(suggestion.options)],
            allow_free_input=True,
            input_hint=suggestion.input_hint,
            auto_submit_single_select=False,
            manual_input_always_visible=False,
        )
        return attach_form_to_ask_user(
            ask_user,
            form=form,
            card_type="ask_form_v2",
            scene=str(context.get("scene") or "").strip() or None,
            extra_context={
                "smart_options_generated": True,
                "smart_options_field": "predicted_model_or_system",
            },
        )

    fields = list(form.iter_fields())
    if len(fields) == 1 and not (fields[0].options or []):
        suggestion = await smart_ask_user_option_enricher.maybe_build_field_suggestion_async(ask_user=ask_user)
        if suggestion is not None and suggestion.options:
            field = fields[0]
            rebuilt_form = build_single_field_form(
                form_id=form.form_id,
                title=form.title,
                description=form.description,
                ask_reason=form.ask_reason,
                field_key=field.key,
                field_label=field.label or suggestion.field_label or "请补充必要信息",
                input_type=AskUserInputType.SINGLE_SELECT,
                options=[option.model_dump(mode="json") for option in to_form_options(suggestion.options)],
                allow_free_input=True,
                input_hint=suggestion.input_hint or field.placeholder or field.hint,
                auto_submit_single_select=bool(form.ui_policy.auto_submit_single_select),
                manual_input_always_visible=bool(field.manual_input and field.manual_input.always_visible),
            )
            rebuilt_form.ui_policy.show_summary_preview = form.ui_policy.show_summary_preview
            rebuilt_form.ui_policy.dense = form.ui_policy.dense
            rebuilt_form.ui_policy.submit_button_text = form.ui_policy.submit_button_text
            rebuilt_form.ui_policy.layout = form.ui_policy.layout
            return attach_form_to_ask_user(
                ask_user,
                form=rebuilt_form,
                card_type=str(context.get("card_type") or "ask_form_v2"),
                scene=str(context.get("scene") or "").strip() or None,
                extra_context={
                    "smart_options_generated": True,
                    "smart_options_field": field.key,
                },
            )

    context["schema_version"] = "2.0"
    context.setdefault("card_type", "ask_form_v2")
    context["form"] = validate_form(form).model_dump(mode="json")
    return ask_user.model_copy(update={"context": context})


def extract_form_answer_fields(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        return {}

    fields: dict[str, dict[str, Any]] = {}
    for key, value in raw_fields.items():
        selected: list[str] = []
        text = ""
        if isinstance(value, dict):
            raw_selected = value.get("selected")
            if isinstance(raw_selected, list):
                selected = [str(item).strip() for item in raw_selected if str(item).strip()]
            elif raw_selected not in (None, ""):
                selected = [str(raw_selected).strip()]
            text = str(value.get("text") or "").strip()
        elif isinstance(value, list):
            selected = [str(item).strip() for item in value if str(item).strip()]
        else:
            text = str(value or "").strip()
        fields[str(key)] = {"selected": selected, "text": text}
    return fields


def extract_primary_answer_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()

    fields = extract_form_answer_fields(payload)
    for item in fields.values():
        selected = item.get("selected") or []
        if selected:
            return str(selected[0]).strip()
        text = str(item.get("text") or "").strip()
        if text:
            return text

    if isinstance(payload, dict):
        return str(payload.get("summary_text") or "").strip()
    return str(payload or "").strip()
