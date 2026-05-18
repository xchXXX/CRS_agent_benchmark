import type { AskUserV2Answers, AskUserV2Condition, AskUserV2Field, AskUserV2Form } from './types'

export function normalizeAnswerValue(value: unknown) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const selected = Array.isArray((value as any).selected)
      ? (value as any).selected.map((item: any) => String(item).trim()).filter(Boolean)
      : []
    const text = typeof (value as any).text === 'string' ? (value as any).text.trim() : ''
    return { selected, text }
  }
  if (Array.isArray(value)) {
    return {
      selected: value.map((item) => String(item).trim()).filter(Boolean),
      text: ''
    }
  }
  return {
    selected: [],
    text: typeof value === 'string' ? value.trim() : ''
  }
}

export function evaluateCondition(condition: AskUserV2Condition, answers: AskUserV2Answers) {
  const answer = normalizeAnswerValue(answers[condition.field])
  const target = String(condition.value ?? '')

  switch (condition.op) {
    case 'equals':
      return answer.text === target || answer.selected.includes(target)
    case 'not_equals':
      return answer.text !== target && !answer.selected.includes(target)
    case 'includes':
      return answer.selected.includes(target)
    case 'not_includes':
      return !answer.selected.includes(target)
    case 'is_truthy':
    case 'is_filled':
      return Boolean(answer.text || answer.selected.length)
    case 'is_empty':
      return !answer.text && answer.selected.length === 0
    default:
      return false
  }
}

export function evaluateConditions(conditions: AskUserV2Condition[] | undefined, answers: AskUserV2Answers) {
  if (!conditions || conditions.length === 0) return true
  return conditions.every((condition) => evaluateCondition(condition, answers))
}

export function isFieldVisible(field: AskUserV2Field, answers: AskUserV2Answers) {
  return evaluateConditions(field.visible_if, answers)
}

export function isFieldSkipped(field: AskUserV2Field, answers: AskUserV2Answers) {
  if (!field.skip_if || field.skip_if.length === 0) return false
  return evaluateConditions(field.skip_if, answers)
}

export function isFieldRequired(field: AskUserV2Field, answers: AskUserV2Answers) {
  if (isFieldSkipped(field, answers)) return false
  if (field.required_if && field.required_if.length > 0) {
    return evaluateConditions(field.required_if, answers)
  }
  return Boolean(field.required)
}

export function flattenVisibleFields(form: AskUserV2Form, answers: AskUserV2Answers) {
  return form.sections.flatMap((section) =>
    section.fields.filter((field) => isFieldVisible(field, answers))
  )
}
