import { flattenVisibleFields, isFieldRequired, isFieldSkipped, normalizeAnswerValue } from './conditionEngine'
import type { AskUserV2Answers, AskUserV2Field, AskUserV2Form } from './types'

export function isFieldAnswered(field: AskUserV2Field, answers: AskUserV2Answers) {
  const answer = normalizeAnswerValue(answers[field.key])
  return Boolean(answer.text || answer.selected.length)
}

export function validateAskUserV2Form(form: AskUserV2Form, answers: AskUserV2Answers) {
  const errors: Record<string, string> = {}

  for (const field of flattenVisibleFields(form, answers)) {
    if (isFieldSkipped(field, answers)) continue
    const answer = normalizeAnswerValue(answers[field.key])
    const hasAnswer = Boolean(answer.text || answer.selected.length)

    if (isFieldRequired(field, answers) && !hasAnswer) {
      errors[field.key] = '这一项是当前继续判断的必填信息。'
      continue
    }

    if (field.validation?.min_items && answer.selected.length < field.validation.min_items) {
      errors[field.key] = `请至少选择 ${field.validation.min_items} 项。`
      continue
    }

    if (field.validation?.max_items && answer.selected.length > field.validation.max_items) {
      errors[field.key] = `最多只能选择 ${field.validation.max_items} 项。`
      continue
    }

    if (field.validation?.min_length && answer.text.length < field.validation.min_length) {
      errors[field.key] = `至少输入 ${field.validation.min_length} 个字。`
      continue
    }

    if (field.validation?.max_length && answer.text.length > field.validation.max_length) {
      errors[field.key] = `最多输入 ${field.validation.max_length} 个字。`
      continue
    }

    if (field.validation?.pattern && answer.text) {
      try {
        const pattern = new RegExp(field.validation.pattern)
        if (!pattern.test(answer.text)) {
          errors[field.key] = '输入格式不符合要求。'
        }
      } catch {
        // ignore invalid regex from backend
      }
    }
  }

  return errors
}
