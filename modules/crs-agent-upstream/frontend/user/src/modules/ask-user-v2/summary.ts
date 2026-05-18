import { flattenVisibleFields, isFieldSkipped, normalizeAnswerValue } from './conditionEngine'
import type { AskUserV2Answers, AskUserV2Field, AskUserV2Form } from './types'

function resolveSelectedLabels(field: AskUserV2Field, selectedValues: string[]) {
  if (!selectedValues.length) {
    return []
  }

  const optionLabelMap = new Map((field.options || []).map((option) => [option.key, option.label]))
  return selectedValues.map((value) => optionLabelMap.get(value) || value)
}

function buildFieldSummary(field: AskUserV2Field, answers: AskUserV2Answers) {
  const answer = normalizeAnswerValue(answers[field.key])
  if (!answer.text && answer.selected.length === 0) {
    return null
  }

  if (field.summary_policy?.use_in_summary === false) {
    return null
  }

  const label = field.summary_policy?.label_override || field.label
  const values = resolveSelectedLabels(field, answer.selected)
  if (answer.text) {
    values.push(answer.text)
  }
  if (values.length === 0) {
    return field.summary_policy?.fallback_text || null
  }
  return `${label}：${values.join('、')}`
}

export function buildAskUserV2Summary(form: AskUserV2Form, answers: AskUserV2Answers) {
  return flattenVisibleFields(form, answers)
    .filter((field) => !isFieldSkipped(field, answers))
    .map((field) => buildFieldSummary(field, answers))
    .filter((item): item is string => Boolean(item))
    .join('；')
}
