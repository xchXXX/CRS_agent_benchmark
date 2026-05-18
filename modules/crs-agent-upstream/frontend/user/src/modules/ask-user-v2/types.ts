import type { AskUserQuestion } from '@/types'

export type AskUserV2FieldType = 'single_select' | 'multi_select' | 'text' | 'number' | 'code_list' | 'file'
export type AskUserV2AnswerMode =
  | 'select_only'
  | 'text_only'
  | 'select_or_text'
  | 'select_and_text'
  | 'number_only'
  | 'file_only'
export type AskUserV2RequiredLevel = 'hard' | 'strong' | 'soft'
export type AskUserV2ConditionOp =
  | 'equals'
  | 'not_equals'
  | 'includes'
  | 'not_includes'
  | 'is_truthy'
  | 'is_filled'
  | 'is_empty'

export interface AskUserV2Condition {
  field: string
  op: AskUserV2ConditionOp
  value?: any
}

export interface AskUserV2OptionEffects {
  show_fields?: string[]
  require_fields?: string[]
  clear_fields?: string[]
  skip_fields?: string[]
}

export interface AskUserV2Option {
  key: string
  label: string
  description?: string
  option_source?: 'system' | 'rule' | 'llm_predicted' | 'user_history'
  evidence_level?: 'confirmed' | 'predicted' | 'weak_hint'
  selection_payload?: Record<string, any>
  effects?: AskUserV2OptionEffects
  tags?: string[]
}

export interface AskUserV2ManualInput {
  enabled: boolean
  always_visible?: boolean
  placeholder?: string
  input_hint?: string
  value_type?: 'text' | 'number' | 'code'
  max_length?: number
}

export interface AskUserV2FieldValidation {
  pattern?: string
  min_length?: number
  max_length?: number
  min_items?: number
  max_items?: number
}

export interface AskUserV2SummaryPolicy {
  use_in_summary?: boolean
  label_override?: string
  fallback_text?: string
}

export interface AskUserV2Field {
  id?: string
  key: string
  label: string
  field_type: AskUserV2FieldType
  answer_mode: AskUserV2AnswerMode
  required?: boolean
  required_level?: AskUserV2RequiredLevel
  placeholder?: string
  hint?: string
  options?: AskUserV2Option[]
  manual_input?: AskUserV2ManualInput | null
  visible_if?: AskUserV2Condition[]
  required_if?: AskUserV2Condition[]
  skip_if?: AskUserV2Condition[]
  validation?: AskUserV2FieldValidation
  summary_policy?: AskUserV2SummaryPolicy
  submit_on_select?: boolean
}

export interface AskUserV2Section {
  id: string
  title: string
  description?: string
  fields: AskUserV2Field[]
}

export interface AskUserV2Action {
  key: string
  label: string
  description?: string
  variant?: 'primary' | 'secondary' | 'ghost'
  action_type?: 'submit' | 'skip' | 'quick_reply'
  payload?: Record<string, any>
}

export interface AskUserV2UiPolicy {
  layout?: 'single_page' | 'stepper'
  auto_submit_single_select?: boolean
  submit_button_text?: string
  show_summary_preview?: boolean
  allow_skip_optional?: boolean
  dense?: boolean
}

export interface AskUserV2Form {
  form_id: string
  version: '2.0'
  mode: 'progressive' | 'single_page'
  title: string
  description?: string
  ask_reason?: string
  sections: AskUserV2Section[]
  actions?: AskUserV2Action[]
  ui_policy?: AskUserV2UiPolicy
  validation_policy?: Record<string, any>
}

export interface AskUserV2AnswerValue {
  selected: string[]
  text: string
}

export type AskUserV2Answers = Record<string, AskUserV2AnswerValue>

export interface AskUserV2FormState {
  toolCallId: string
  question: string
  form: AskUserV2Form
  status: 'active' | 'submitting' | 'submitted' | 'archived'
  summaryText?: string
  originalQuery?: string
  scene?: string
}

export interface AskUserV2Submission {
  action: string
  formId: string
  fields: AskUserV2Answers
  summaryText: string
  selectionPayload?: Record<string, any>
  actionPayload?: Record<string, any>
}

export function isAskUserV2Question(question: AskUserQuestion | null | undefined): boolean {
  return Boolean(
    question
    && question.context
    && question.context.schema_version === '2.0'
    && question.context.form
  )
}

export function getAskUserV2Form(question: AskUserQuestion | null | undefined): AskUserV2Form | null {
  if (!isAskUserV2Question(question)) {
    return null
  }
  return question?.context?.form as AskUserV2Form
}

export function emptyAnswerValue(): AskUserV2AnswerValue {
  return { selected: [], text: '' }
}
