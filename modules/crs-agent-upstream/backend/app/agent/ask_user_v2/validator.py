"""Validation helpers for Ask User v2 forms."""

from __future__ import annotations

from app.agent.ask_user_v2.schema import AskUserForm, AskUserFormField, AskUserFormSection


def _validate_field(field: AskUserFormField, known_keys: set[str]) -> None:
    if not field.key.strip():
        raise ValueError("Ask User v2 field key cannot be empty.")

    if field.field_type in {"single_select", "multi_select"} and field.answer_mode != "text_only" and not field.options:
        raise ValueError(f"Ask User v2 field `{field.key}` requires options.")

    if field.answer_mode == "select_only" and field.manual_input and field.manual_input.enabled:
        raise ValueError(f"Ask User v2 field `{field.key}` cannot enable manual_input for select_only mode.")

    if field.answer_mode == "text_only" and field.options:
        raise ValueError(f"Ask User v2 field `{field.key}` cannot define options in text_only mode.")

    for group_name in ("visible_if", "required_if", "skip_if"):
        for condition in getattr(field, group_name):
            if condition.field not in known_keys:
                raise ValueError(
                    f"Ask User v2 field `{field.key}` references unknown condition field `{condition.field}`."
                )


def validate_form(form: AskUserForm) -> AskUserForm:
    if not form.sections:
        raise ValueError("Ask User v2 form requires at least one section.")

    field_keys: list[str] = []
    for section in form.sections:
        if not isinstance(section, AskUserFormSection):
            raise ValueError("Ask User v2 section is invalid.")
        field_keys.extend(field.key for field in section.fields)

    duplicate_field_keys = {key for key in field_keys if field_keys.count(key) > 1}
    if duplicate_field_keys:
        raise ValueError(f"Ask User v2 field keys must be unique: {sorted(duplicate_field_keys)}")

    action_keys = [action.key for action in form.actions]
    duplicate_action_keys = {key for key in action_keys if action_keys.count(key) > 1}
    if duplicate_action_keys:
        raise ValueError(f"Ask User v2 action keys must be unique: {sorted(duplicate_action_keys)}")

    overlap = set(field_keys) & set(action_keys)
    if overlap:
        raise ValueError(f"Ask User v2 field keys and action keys must be distinct: {sorted(overlap)}")

    known_keys = set(field_keys)
    for field in form.iter_fields():
        _validate_field(field, known_keys)

    return form
