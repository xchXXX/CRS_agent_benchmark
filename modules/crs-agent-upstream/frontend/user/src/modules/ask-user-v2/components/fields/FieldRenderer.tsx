import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Send } from 'lucide-react'

import { normalizeAnswerValue } from '../../conditionEngine'
import type { AskUserV2AnswerValue, AskUserV2Field } from '../../types'

interface FieldRendererProps {
  field: AskUserV2Field
  value: AskUserV2AnswerValue
  error?: string
  disabled?: boolean
  appearance?: 'default' | 'wizard' | 'compact'
  onRequestSubmit?: () => void
  onChange: (value: AskUserV2AnswerValue) => void
}

function allowsManualInput(field: AskUserV2Field) {
  return Boolean(field.manual_input?.enabled)
}

export default function FieldRenderer({
  field,
  value,
  error,
  disabled = false,
  appearance = 'default',
  onRequestSubmit,
  onChange,
}: FieldRendererProps) {
  const normalizedValue = normalizeAnswerValue(value) as AskUserV2AnswerValue
  const [manualOpen, setManualOpen] = useState(false)
  const hasOptions = (field.options?.length || 0) > 0
  const hasOptionDescriptions = Boolean(field.options?.some((option) => option.description))
  const manualEnabled = allowsManualInput(field)
  const manualAlwaysVisible = Boolean(field.manual_input?.always_visible)
  const useWizardAppearance = appearance === 'wizard'
  const useCompactAppearance = appearance === 'compact'
  const useListAppearance = useWizardAppearance || useCompactAppearance
  const compactOptions = hasOptions && !hasOptionDescriptions && (field.options?.length || 0) <= 6
  const showManualInput = useMemo(() => {
    if (!manualEnabled) return field.field_type === 'text' || field.answer_mode === 'text_only'
    if (manualAlwaysVisible) return true
    if (!hasOptions) return true
    return manualOpen || Boolean(normalizedValue.text)
  }, [field.answer_mode, field.field_type, hasOptions, manualAlwaysVisible, manualEnabled, manualOpen, normalizedValue.text])

  useEffect(() => {
    if (!manualEnabled && !hasOptions) {
      setManualOpen(true)
    }
  }, [hasOptions, manualEnabled])

  const updateText = (text: string) => {
    onChange({
      selected: normalizedValue.selected,
      text,
    })
  }

  const toggleOption = (optionKey: string) => {
    const isSingle = field.field_type === 'single_select'
    const nextSelected = isSingle
      ? [optionKey]
      : normalizedValue.selected.includes(optionKey)
        ? normalizedValue.selected.filter((item) => item !== optionKey)
        : [...normalizedValue.selected, optionKey]

    const nextValue = {
      selected: nextSelected,
      text: field.answer_mode === 'select_only' ? '' : normalizedValue.text,
    }
    onChange(nextValue)
  }

  return (
    <div className={`ask-user-v2-field ${useWizardAppearance ? 'ask-user-v2-field--wizard' : ''} ${useCompactAppearance ? 'ask-user-v2-field--compact-panel' : ''}`}>
      <div className="ask-user-v2-field-header">
        <div className="ask-user-v2-field-title-row">
          <div className="ask-user-v2-field-title">{field.label}</div>
          {field.required && (
            <span className={`ask-user-v2-field-required ask-user-v2-field-required--${field.required_level || 'strong'}`}>
              {field.required_level === 'hard' ? '必须' : field.required_level === 'soft' ? '可选' : '建议'}
            </span>
          )}
        </div>
        {field.hint && <div className="ask-user-v2-field-hint">{field.hint}</div>}
      </div>

      {hasOptions && (
        <div className={`${useListAppearance ? 'wizard-options ask-user-v2-option-grid--wizard' : 'ask-user-v2-option-grid'} ${compactOptions ? 'ask-user-v2-option-grid--compact' : ''} ${useCompactAppearance ? 'ask-user-v2-option-grid--panel' : ''}`}>
          {field.options?.map((option, index) => {
            const active = normalizedValue.selected.includes(option.key)
            return (
              <button
                key={option.key}
                type="button"
                className={`${useListAppearance ? 'wizard-option ask-user-v2-option--wizard' : 'ask-user-v2-option'} ${compactOptions ? 'ask-user-v2-option--compact' : ''} ${useCompactAppearance ? 'ask-user-v2-option--panel' : ''} ${active ? 'is-active' : ''}`}
                onClick={() => toggleOption(option.key)}
                disabled={disabled}
              >
                {useListAppearance ? (
                  <>
                    <span className="option-index">{active ? '✓' : index + 1}</span>
                    <span className="option-content">
                      <span className="option-text">{option.label}</span>
                      {option.description && <span className="option-desc">{option.description}</span>}
                    </span>
                    <ChevronRight size={16} className="option-arrow" />
                  </>
                ) : (
                  <>
                    <span className="ask-user-v2-option-label">{option.label}</span>
                    {option.description && <span className="ask-user-v2-option-desc">{option.description}</span>}
                    <span className="ask-user-v2-option-state">{active ? '已选' : field.field_type === 'multi_select' ? '多选' : '单选'}</span>
                  </>
                )}
              </button>
            )
          })}
        </div>
      )}

      {manualEnabled && hasOptions && !manualAlwaysVisible && (
        <button
          type="button"
          className={`ask-user-v2-manual-trigger ${showManualInput ? 'is-open' : ''}`}
          onClick={() => setManualOpen((prev) => !prev)}
          disabled={disabled}
        >
          {showManualInput ? '收起补充输入' : '没有合适项，我来补充'}
        </button>
      )}

      {showManualInput && (
        useListAppearance ? (
          <div className="wizard-free-input ask-user-v2-wizard-free-input">
            <div className="wizard-free-input-box">
              <input
                type="text"
                className="ecu-input wizard-free-input-control ask-user-v2-inline-input"
                value={normalizedValue.text}
                onChange={(event) => updateText(event.target.value)}
                placeholder={field.manual_input?.placeholder || field.placeholder || `补充${field.label}`}
                maxLength={field.manual_input?.max_length}
                disabled={disabled}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && normalizedValue.text.trim() && !disabled) {
                    onRequestSubmit?.()
                  }
                }}
              />
              <button
                type="button"
                className="ecu-submit-btn ask-user-v2-inline-submit"
                onClick={() => onRequestSubmit?.()}
                disabled={disabled || !normalizedValue.text.trim()}
              >
                <Send size={15} />
              </button>
            </div>
          </div>
        ) : (
          <textarea
            className="ask-user-v2-textarea"
            value={normalizedValue.text}
            onChange={(event) => updateText(event.target.value)}
            placeholder={field.manual_input?.placeholder || field.placeholder || `补充${field.label}`}
            maxLength={field.manual_input?.max_length}
            rows={3}
            disabled={disabled}
          />
        )
      )}

      {error && <div className="ask-user-v2-field-error">{error}</div>}
    </div>
  )
}
