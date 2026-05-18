"""Schema definitions for Ask User v2."""

from __future__ import annotations

from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field


AskUserFieldType = Literal["single_select", "multi_select", "text", "number", "code_list", "file"]
AskUserAnswerMode = Literal[
    "select_only",
    "text_only",
    "select_or_text",
    "select_and_text",
    "number_only",
    "file_only",
]
AskUserRequiredLevel = Literal["hard", "strong", "soft"]
AskUserOptionSource = Literal["system", "rule", "llm_predicted", "user_history"]
AskUserEvidenceLevel = Literal["confirmed", "predicted", "weak_hint"]
AskUserActionVariant = Literal["primary", "secondary", "ghost"]
AskUserActionType = Literal["submit", "skip", "quick_reply"]
AskUserConditionOp = Literal[
    "equals",
    "not_equals",
    "includes",
    "not_includes",
    "is_truthy",
    "is_filled",
    "is_empty",
]


class AskUserFormCondition(BaseModel):
    field: str
    op: AskUserConditionOp = "equals"
    value: Any = None


class AskUserFormOptionEffects(BaseModel):
    show_fields: list[str] = Field(default_factory=list)
    require_fields: list[str] = Field(default_factory=list)
    clear_fields: list[str] = Field(default_factory=list)
    skip_fields: list[str] = Field(default_factory=list)


class AskUserFormOption(BaseModel):
    key: str
    label: str
    description: str | None = None
    option_source: AskUserOptionSource = "system"
    evidence_level: AskUserEvidenceLevel = "confirmed"
    selection_payload: dict[str, Any] = Field(default_factory=dict)
    effects: AskUserFormOptionEffects = Field(default_factory=AskUserFormOptionEffects)
    tags: list[str] = Field(default_factory=list)


class AskUserFormManualInput(BaseModel):
    enabled: bool = False
    always_visible: bool = False
    placeholder: str | None = None
    input_hint: str | None = None
    value_type: Literal["text", "number", "code"] = "text"
    max_length: int | None = None


class AskUserFormFieldValidation(BaseModel):
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None


class AskUserFormSummaryPolicy(BaseModel):
    use_in_summary: bool = True
    label_override: str | None = None
    fallback_text: str | None = None


class AskUserFormField(BaseModel):
    id: str | None = None
    key: str
    label: str
    field_type: AskUserFieldType = "text"
    answer_mode: AskUserAnswerMode = "text_only"
    required: bool = False
    required_level: AskUserRequiredLevel = "strong"
    placeholder: str | None = None
    hint: str | None = None
    options: list[AskUserFormOption] = Field(default_factory=list)
    manual_input: AskUserFormManualInput | None = None
    visible_if: list[AskUserFormCondition] = Field(default_factory=list)
    required_if: list[AskUserFormCondition] = Field(default_factory=list)
    skip_if: list[AskUserFormCondition] = Field(default_factory=list)
    validation: AskUserFormFieldValidation = Field(default_factory=AskUserFormFieldValidation)
    summary_policy: AskUserFormSummaryPolicy = Field(default_factory=AskUserFormSummaryPolicy)
    submit_on_select: bool = False


class AskUserFormSection(BaseModel):
    id: str
    title: str
    description: str | None = None
    fields: list[AskUserFormField] = Field(default_factory=list)


class AskUserFormAction(BaseModel):
    key: str
    label: str
    description: str | None = None
    variant: AskUserActionVariant = "secondary"
    action_type: AskUserActionType = "quick_reply"
    payload: dict[str, Any] = Field(default_factory=dict)


class AskUserFormUiPolicy(BaseModel):
    layout: Literal["single_page", "stepper"] = "single_page"
    auto_submit_single_select: bool = False
    submit_button_text: str | None = None
    show_summary_preview: bool = True
    allow_skip_optional: bool = True
    dense: bool = False


class AskUserForm(BaseModel):
    form_id: str
    version: Literal["2.0"] = "2.0"
    mode: Literal["progressive", "single_page"] = "single_page"
    title: str
    description: str | None = None
    ask_reason: str | None = None
    sections: list[AskUserFormSection] = Field(default_factory=list)
    actions: list[AskUserFormAction] = Field(default_factory=list)
    ui_policy: AskUserFormUiPolicy = Field(default_factory=AskUserFormUiPolicy)
    validation_policy: dict[str, Any] = Field(default_factory=dict)

    def iter_fields(self) -> Iterable[AskUserFormField]:
        for section in self.sections:
            yield from section.fields

    def field_map(self) -> dict[str, AskUserFormField]:
        return {field.key: field for field in self.iter_fields()}


def extract_ask_user_form(context: dict[str, Any] | None) -> AskUserForm | None:
    if not isinstance(context, dict):
        return None
    raw_form = context.get("form")
    if not isinstance(raw_form, dict):
        return None
    return AskUserForm.model_validate(raw_form)
