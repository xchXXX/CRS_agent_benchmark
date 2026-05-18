import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Send } from 'lucide-react'

import { flattenVisibleFields, isFieldRequired, normalizeAnswerValue } from '../conditionEngine'
import { buildAskUserV2Summary } from '../summary'
import type { AskUserV2Answers, AskUserV2Field, AskUserV2Form, AskUserV2Submission } from '../types'
import { emptyAnswerValue } from '../types'
import { isFieldAnswered, validateAskUserV2Form } from '../validation'
import FieldRenderer from './fields/FieldRenderer'

interface AskUserFormV2Props {
  question: string
  form: AskUserV2Form
  compactPanel?: boolean
  disabled?: boolean
  onSubmit: (submission: AskUserV2Submission) => Promise<boolean> | boolean
}

function buildDefaultAnswers(form: AskUserV2Form): AskUserV2Answers {
  return form.sections.reduce<AskUserV2Answers>((acc, section) => {
    section.fields.forEach((field) => {
      acc[field.key] = emptyAnswerValue()
    })
    return acc
  }, {})
}

function resolveSelectionPayload(form: AskUserV2Form, answers: AskUserV2Answers) {
  for (const field of flattenVisibleFields(form, answers)) {
    const answer = normalizeAnswerValue(answers[field.key])
    if (!answer.selected.length) continue
    const selectedOption = field.options?.find((option) => option.key === answer.selected[0])
    if (selectedOption?.selection_payload) {
      return selectedOption.selection_payload
    }
  }
  return undefined
}

function validateCurrentField(form: AskUserV2Form, fieldKey: string, answers: AskUserV2Answers) {
  const field = flattenVisibleFields(form, answers).find((item) => item.key === fieldKey)
  if (!field) {
    return ''
  }

  const answer = normalizeAnswerValue(answers[field.key])
  const hasAnswer = Boolean(answer.text || answer.selected.length)
  if (isFieldRequired(field, answers) && !hasAnswer) {
    return '这一项是当前继续判断的必填信息。'
  }
  if (field.validation?.min_items && answer.selected.length < field.validation.min_items) {
    return `请至少选择 ${field.validation.min_items} 项。`
  }
  if (field.validation?.max_items && answer.selected.length > field.validation.max_items) {
    return `最多只能选择 ${field.validation.max_items} 项。`
  }
  if (field.validation?.min_length && answer.text.length < field.validation.min_length) {
    return `至少输入 ${field.validation.min_length} 个字。`
  }
  if (field.validation?.max_length && answer.text.length > field.validation.max_length) {
    return `最多输入 ${field.validation.max_length} 个字。`
  }
  if (field.validation?.pattern && answer.text) {
    try {
      const pattern = new RegExp(field.validation.pattern)
      if (!pattern.test(answer.text)) {
        return '输入格式不符合要求。'
      }
    } catch {
      return ''
    }
  }
  return ''
}

function shouldAutoAdvanceSingleSelectField(
  form: AskUserV2Form,
  field: AskUserV2Field,
  {
    useProgressiveFlow,
    useWizardAppearance,
  }: {
    useProgressiveFlow: boolean
    useWizardAppearance: boolean
  },
) {
  if (field.field_type !== 'single_select') {
    return false
  }
  if (field.manual_input?.enabled) {
    return false
  }
  if (typeof field.submit_on_select === 'boolean') {
    return field.submit_on_select
  }
  if (useProgressiveFlow) {
    return true
  }
  return Boolean(useWizardAppearance || form.ui_policy?.auto_submit_single_select)
}

function normalizeHeaderText(value: string | undefined | null) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function isGenericSupplementQuestion(value: string) {
  return /^(请先补充(?:以下)?关键信息|请补充必要信息|请先补充必要信息)$/.test(value)
}

function isGenericSupplementDescription(value: string) {
  return /^优先点选.*?(?:没有合适项时|若没有合适选项再|没有合适项再|不在候选里时再|再)手动补充。?$/.test(value)
}

function isGenericCompactPanelTitle(value: string) {
  return /^(维修问答补充|参数查询补充|补充信息)$/.test(value)
}

function isDuplicateHeaderText(base: string, candidate: string) {
  if (!base || !candidate) return false
  if (base === candidate) return true
  if (base.length >= 8 && candidate.includes(base)) return true
  if (candidate.length >= 8 && base.includes(candidate)) return true
  return false
}

function isGenericAskReason(value: string, visibleLabels: string[]) {
  if (!/^还缺少\s*.+\s*等关键信息，补充后才能继续缩小范围。?$/.test(value)) {
    return false
  }

  const normalizedReason = value.replace(/\s+/g, '')
  const normalizedLabels = visibleLabels
    .map((label) => normalizeHeaderText(label).replace(/\s+/g, ''))
    .filter(Boolean)

  if (!normalizedLabels.length) {
    return true
  }

  return normalizedLabels.some((label) => normalizedReason.includes(label))
}

export default function AskUserFormV2({
  question,
  form,
  compactPanel = false,
  disabled = false,
  onSubmit,
}: AskUserFormV2Props) {
  const [answers, setAnswers] = useState<AskUserV2Answers>(() => buildDefaultAnswers(form))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [activeStep, setActiveStep] = useState(0)

  useEffect(() => {
    setAnswers(buildDefaultAnswers(form))
    setErrors({})
    setActiveStep(0)
  }, [form.form_id])

  const visibleFields = useMemo(() => flattenVisibleFields(form, answers), [answers, form])
  const summaryText = useMemo(() => buildAskUserV2Summary(form, answers), [answers, form])
  const isSingleFieldForm = form.sections.length === 1 && visibleFields.length === 1
  const singleField = isSingleFieldForm ? visibleFields[0] : null
  const useWizardAppearance = Boolean(isSingleFieldForm && (singleField?.options?.length || 0) > 0)
  const useProgressiveFlow = Boolean(
    !useWizardAppearance
    && visibleFields.length > 1
    && (form.mode === 'progressive' || form.ui_policy?.layout === 'stepper')
  )
  const useCompactPanelLayout = Boolean(compactPanel)
  const normalizedTitle = normalizeHeaderText(form.title)
  const normalizedQuestion = normalizeHeaderText(question)
  const normalizedDescription = normalizeHeaderText(form.description)
  const normalizedReason = normalizeHeaderText(form.ask_reason)
  const visibleFieldLabels = visibleFields.map((field) => field.label || '')
  const showKicker = Boolean(
    normalizedTitle
    && !(useCompactPanelLayout && isGenericCompactPanelTitle(normalizedTitle) && (normalizedQuestion || normalizedDescription || normalizedReason))
  )
  const hideQuestion = Boolean(
    singleField
    && normalizedQuestion
    && normalizedQuestion === normalizeHeaderText(singleField.label)
  )
  const showQuestion = Boolean(
    !hideQuestion
    && normalizedQuestion
    && !(useCompactPanelLayout && isGenericSupplementQuestion(normalizedQuestion))
  )
  const showDescription = Boolean(
    normalizedDescription
    && !(useCompactPanelLayout && isGenericSupplementDescription(normalizedDescription))
    && !isDuplicateHeaderText(normalizedQuestion, normalizedDescription)
  )
  const showReason = Boolean(
    normalizedReason
    && !isDuplicateHeaderText(normalizedQuestion, normalizedReason)
    && !isDuplicateHeaderText(normalizedDescription, normalizedReason)
    && !(useCompactPanelLayout && isGenericAskReason(normalizedReason, visibleFieldLabels))
  )
  const hasHeaderCopy = Boolean(showKicker || showQuestion || showDescription || showReason)
  const hideFooterForAutoSubmitWizard = Boolean(
    useWizardAppearance
    && singleField
    && shouldAutoAdvanceSingleSelectField(form, singleField, {
      useProgressiveFlow,
      useWizardAppearance,
    })
  )
  const hasAnswer = visibleFields.some((field) => {
    const answer = normalizeAnswerValue(answers[field.key])
    return Boolean(answer.text || answer.selected.length)
  })
  const safeStepIndex = useProgressiveFlow
    ? Math.min(activeStep, Math.max(visibleFields.length - 1, 0))
    : 0
  const currentField = useProgressiveFlow ? visibleFields[safeStepIndex] || null : null
  const visibleFieldKeys = new Set(
    useProgressiveFlow && currentField
      ? [currentField.key]
      : visibleFields.map((field) => field.key)
  )
  const currentAnswered = currentField ? isFieldAnswered(currentField, answers) : false
  const canMoveForward = currentField ? !isFieldRequired(currentField, answers) || currentAnswered : hasAnswer
  const isLastStep = currentField ? safeStepIndex >= visibleFields.length - 1 : false
  const progressPercent = useProgressiveFlow && visibleFields.length > 0
    ? ((safeStepIndex + 1) / visibleFields.length) * 100
    : 0
  const showHeader = Boolean(hasHeaderCopy || currentField)

  useEffect(() => {
    if (!useProgressiveFlow) {
      if (activeStep !== 0) {
        setActiveStep(0)
      }
      return
    }
    if (activeStep > visibleFields.length - 1) {
      setActiveStep(Math.max(visibleFields.length - 1, 0))
    }
  }, [activeStep, useProgressiveFlow, visibleFields.length])

  const submitAnswers = async (submittedAnswers: AskUserV2Answers) => {
    const nextErrors = validateAskUserV2Form(form, submittedAnswers)
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) {
      return false
    }

    const nextSummary = buildAskUserV2Summary(form, submittedAnswers)
    const success = await onSubmit({
      action: 'submit',
      formId: form.form_id,
      fields: submittedAnswers,
      summaryText: nextSummary,
      selectionPayload: resolveSelectionPayload(form, submittedAnswers),
    })
    return success
  }

  const submitCurrentAnswers = async () => submitAnswers(answers)
  const submitAction = async (actionKey: string, actionLabel: string, actionPayload?: Record<string, any>) => {
    const summary = `用户选择：${actionLabel}`
    return onSubmit({
      action: actionKey,
      formId: form.form_id,
      fields: answers,
      summaryText: summary,
      selectionPayload: resolveSelectionPayload(form, answers),
      actionPayload,
    })
  }
  const handleStepAdvance = async () => {
    if (!currentField) {
      return false
    }

    const currentFieldError = validateCurrentField(form, currentField.key, answers)
    if (currentFieldError) {
      setErrors((prev) => ({ ...prev, [currentField.key]: currentFieldError }))
      return false
    }

    if (isLastStep) {
      return submitCurrentAnswers()
    }

    setActiveStep((prev) => Math.min(prev + 1, visibleFields.length - 1))
    return true
  }

  return (
    <div
      className={`ask-user-v2-form ${isSingleFieldForm ? 'ask-user-v2-form--compact' : ''} ${useWizardAppearance ? 'ask-user-v2-form--wizard' : ''} ${useCompactPanelLayout ? 'ask-user-v2-form--compact-panel' : ''}`}
    >
      {showHeader && (
        <div className={`ask-user-v2-form-header ${!hasHeaderCopy ? 'ask-user-v2-form-header--meta-only' : ''}`}>
          {hasHeaderCopy && (
            <div className="ask-user-v2-form-header-row">
              <div className="ask-user-v2-form-header-copy">
                {showKicker && <div className="ask-user-v2-form-kicker">{form.title}</div>}
                {showQuestion && <div className="ask-user-v2-form-question">{question}</div>}
                {showDescription && <div className="ask-user-v2-form-description">{form.description}</div>}
                {showReason && <div className="ask-user-v2-form-reason">{form.ask_reason}</div>}
              </div>
            </div>
          )}
          {useProgressiveFlow && currentField && (
            <>
              <div className="ask-user-v2-stepper-meta">
                <span>{safeStepIndex + 1} / {visibleFields.length}</span>
              </div>
              <div className="ask-user-v2-stepper-progress" aria-hidden="true">
                <span style={{ width: `${progressPercent}%` }} />
              </div>
            </>
          )}
        </div>
      )}

      <div className="ask-user-v2-form-body">
        {form.sections.map((section) => {
          const fields = section.fields.filter((field) => visibleFieldKeys.has(field.key))
          if (fields.length === 0) return null
          const isSingleFieldSection = fields.length === 1 && (isSingleFieldForm || useProgressiveFlow)
          const showSectionHeader = !isSingleFieldSection
          return (
            <section
              key={section.id}
              className={`ask-user-v2-section ${isSingleFieldSection ? 'ask-user-v2-section--compact' : ''} ${useWizardAppearance ? 'ask-user-v2-section--wizard' : ''}`}
            >
              {showSectionHeader && (
                <div className="ask-user-v2-section-header">
                  <div className="ask-user-v2-section-title">{section.title}</div>
                  {section.description && <div className="ask-user-v2-section-description">{section.description}</div>}
                </div>
              )}
              <div className="ask-user-v2-section-fields">
                {fields.map((field) => (
                  <FieldRenderer
                    key={field.key}
                    field={field}
                    value={answers[field.key] || emptyAnswerValue()}
                    error={errors[field.key]}
                    disabled={disabled}
                    appearance={useWizardAppearance ? 'wizard' : useCompactPanelLayout && isSingleFieldSection ? 'compact' : 'default'}
                    onRequestSubmit={() => {
                      if (useProgressiveFlow) {
                        void handleStepAdvance()
                        return
                      }
                      void submitAnswers({ ...answers })
                    }}
                    onChange={(value) => {
                      const nextAnswers = { ...answers, [field.key]: value }
                      setAnswers(nextAnswers)
                      setErrors((prev) => {
                        if (!prev[field.key]) return prev
                        const next = { ...prev }
                        delete next[field.key]
                        return next
                      })

                      const shouldAutoAdvance = shouldAutoAdvanceSingleSelectField(form, field, {
                        useProgressiveFlow,
                        useWizardAppearance,
                      })

                      if (
                        shouldAutoAdvance
                        && value.selected.length > 0
                        && useWizardAppearance
                        && !value.text.trim()
                      ) {
                        setTimeout(() => {
                          void submitAnswers(nextAnswers)
                        }, 0)
                        return
                      }

                      if (
                        useProgressiveFlow
                        && field.key === currentField?.key
                        && shouldAutoAdvance
                      ) {
                        setTimeout(() => {
                          if (safeStepIndex >= visibleFields.length - 1) {
                            void submitAnswers(nextAnswers)
                            return
                          }
                          setActiveStep((prev) => Math.min(prev + 1, visibleFields.length - 1))
                        }, 0)
                        return
                      }

                      if (shouldAutoAdvance) {
                        setTimeout(() => {
                          void submitAnswers(nextAnswers)
                        }, 0)
                      }
                    }}
                  />
                ))}
              </div>
            </section>
          )
        })}
      </div>

      {Boolean(form.actions?.length) && (
        <div className="ask-user-v2-action-row">
          {form.actions?.map((action) => (
            <button
              key={action.key}
              type="button"
              className="ask-user-v2-action"
              onClick={() => void submitAction(action.key, action.label, action.payload)}
              disabled={disabled}
            >
              <span className="ask-user-v2-action-label">{action.label}</span>
              {action.description && <span className="ask-user-v2-action-desc">{action.description}</span>}
            </button>
          ))}
        </div>
      )}

      {form.ui_policy?.show_summary_preview !== false && summaryText && !useCompactPanelLayout && (
        <div className="ask-user-v2-summary-preview">{summaryText}</div>
      )}

      {!hideFooterForAutoSubmitWizard && (
        useProgressiveFlow && currentField ? (
          <div className="ask-user-v2-form-footer ask-user-v2-form-footer--stepper">
            <button
              type="button"
              className="ask-user-v2-back"
              onClick={() => setActiveStep((prev) => Math.max(prev - 1, 0))}
              disabled={disabled || safeStepIndex === 0}
            >
              <ChevronLeft size={15} />
              <span>上一步</span>
            </button>
            <button
              type="button"
              className="ask-user-v2-submit"
              onClick={() => void handleStepAdvance()}
              disabled={disabled || !canMoveForward || (isLastStep && !hasAnswer)}
            >
              <span>{isLastStep ? (form.ui_policy?.submit_button_text || '继续分析') : '下一步'}</span>
              {disabled && isLastStep ? <Send size={15} /> : <ChevronRight size={15} />}
            </button>
          </div>
        ) : (
          <div className="ask-user-v2-form-footer">
            <button
              type="button"
              className="ask-user-v2-submit"
              onClick={() => void submitCurrentAnswers()}
              disabled={disabled || !hasAnswer}
            >
              <span>{form.ui_policy?.submit_button_text || '继续分析'}</span>
              {disabled ? <Send size={15} /> : <ChevronRight size={15} />}
            </button>
          </div>
        )
      )}
    </div>
  )
}
