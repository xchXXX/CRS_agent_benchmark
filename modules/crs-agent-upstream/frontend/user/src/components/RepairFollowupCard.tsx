import { useEffect, useState } from 'react'
import { Archive, ChevronLeft, ChevronRight, CircleCheckBig, Send } from 'lucide-react'

import type { RepairKnowledgeSourceRef } from '@/types'

export interface RepairFollowupQuickAction {
  key: string
  label: string
}

export interface RepairFollowupFieldGroupState {
  key: string
  label: string
  requiredLevel: 'hard' | 'strong' | 'soft'
  selectionMode: 'single' | 'multi' | 'mixed'
  presets: string[]
  selectedPresets: string[]
  textValue: string
  placeholder?: string
  hint?: string
}

export interface RepairFollowupState {
  toolCallId: string
  question: string
  originalQuery: string
  status: 'active' | 'submitting' | 'submitted' | 'archived'
  sourceRefs: RepairKnowledgeSourceRef[]
  askReason?: string
  groups: RepairFollowupFieldGroupState[]
  quickActions: RepairFollowupQuickAction[]
  summaryText?: string
}

interface RepairFollowupCardProps {
  state: RepairFollowupState
  isLoading?: boolean
  onTogglePreset: (groupKey: string, preset: string) => void
  onTextChange: (groupKey: string, value: string) => void
  onSubmit: () => void
  onQuickAction: (actionKey: string) => void
}

function getRequiredLabel(level: RepairFollowupFieldGroupState['requiredLevel']) {
  if (level === 'hard') return '必须'
  if (level === 'strong') return '建议'
  return '可选'
}

function buildDraftSummary(groups: RepairFollowupFieldGroupState[]) {
  const parts = groups
    .map((group) => {
      const selected = group.selectedPresets.join('、')
      const text = group.textValue.trim()
      if (!selected && !text) return null
      if (selected && text) return `${group.label}：${selected}；${text}`
      return `${group.label}：${selected || text}`
    })
    .filter((item): item is string => Boolean(item))

  return parts.join('；')
}

function buildArchivedQueryPreview(query: string) {
  const normalized = query.replace(/\s+/g, ' ').trim()
  if (!normalized) return ''
  if (normalized.length <= 20) return normalized
  return `${normalized.slice(0, 20)}...`
}

function isGroupAnswered(group: RepairFollowupFieldGroupState) {
  return Boolean(group.selectedPresets.length || group.textValue.trim())
}

export default function RepairFollowupCard({
  state,
  isLoading = false,
  onTogglePreset,
  onTextChange,
  onSubmit,
  onQuickAction,
}: RepairFollowupCardProps) {
  const [activeStep, setActiveStep] = useState(0)
  const [manualInputOpen, setManualInputOpen] = useState<Record<string, boolean>>({})
  const disabled = isLoading
  const draftSummary = buildDraftSummary(state.groups)
  const displaySummary = state.summaryText || draftSummary
  const totalSteps = state.groups.length
  const safeStepIndex = totalSteps > 0 ? Math.min(activeStep, totalSteps - 1) : 0
  const currentGroup = totalSteps > 0 ? state.groups[safeStepIndex] : null
  const archivedPreview = buildArchivedQueryPreview(state.originalQuery)
  const currentAnswered = currentGroup ? isGroupAnswered(currentGroup) : false
  const isLastStep = safeStepIndex >= totalSteps - 1
  const hasPresetChoices = Boolean(currentGroup && currentGroup.presets.length > 0)
  const forceManualInput = currentGroup?.key === 'fault_codes'
  const showManualInput = Boolean(
    currentGroup && (
      !hasPresetChoices
      || forceManualInput
      || manualInputOpen[currentGroup.key]
      || currentGroup.textValue.trim()
    )
  )

  useEffect(() => {
    setActiveStep(0)
    setManualInputOpen({})
  }, [state.toolCallId])

  useEffect(() => {
    if (totalSteps > 0 && activeStep > totalSteps - 1) {
      setActiveStep(totalSteps - 1)
    }
  }, [activeStep, totalSteps])

  useEffect(() => {
    if (!currentGroup || currentGroup.presets.length > 0) return
    setManualInputOpen((prev) => (
      prev[currentGroup.key]
        ? prev
        : { ...prev, [currentGroup.key]: true }
    ))
  }, [currentGroup])

  if (state.status === 'archived') {
    return (
      <div className="repair-followup-card repair-followup-card--archived">
        <div className="repair-followup-summary">
          <Archive size={14} className="repair-followup-summary-icon repair-followup-summary-icon--archived" />
          <div className="repair-followup-summary-body">
            <div className="repair-followup-summary-title repair-followup-summary-title--archived">维修补充已归档</div>
            {archivedPreview && (
              <div className="repair-followup-summary-text repair-followup-summary-text--archived" title={state.originalQuery}>
                {archivedPreview}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (state.status === 'submitted') {
    return (
      <div className="repair-followup-card repair-followup-card--submitted">
        <div className="repair-followup-summary">
          <CircleCheckBig size={18} className="repair-followup-summary-icon repair-followup-summary-icon--done" />
          <div className="repair-followup-summary-body">
            <div className="repair-followup-summary-title">已补充维修信息</div>
            <div className="repair-followup-summary-text">{displaySummary || state.question}</div>
          </div>
        </div>
      </div>
    )
  }

  if (state.status === 'submitting') {
    return (
      <div className="repair-followup-card repair-followup-card--submitted">
        <div className="repair-followup-summary">
          <CircleCheckBig size={18} className="repair-followup-summary-icon repair-followup-summary-icon--done" />
          <div className="repair-followup-summary-body">
            <div className="repair-followup-summary-title">已提交维修信息，正在分析</div>
            <div className="repair-followup-summary-text">{displaySummary || state.question}</div>
          </div>
        </div>
      </div>
    )
  }

  if (!currentGroup) {
    return null
  }

  const canMoveForward = currentGroup.requiredLevel !== 'hard' || currentAnswered
  const progressPercent = totalSteps > 0 ? ((safeStepIndex + 1) / totalSteps) * 100 : 0
  const primaryDisabled = disabled || !canMoveForward || (isLastStep && !draftSummary)

  const handleNext = () => {
    if (primaryDisabled) return

    if (isLastStep) {
      onSubmit()
      return
    }

    setActiveStep((prev) => Math.min(prev + 1, totalSteps - 1))
  }

  const handleBack = () => {
    if (disabled) return
    setActiveStep((prev) => Math.max(prev - 1, 0))
  }

  const toggleManualInput = () => {
    setManualInputOpen((prev) => ({
      ...prev,
      [currentGroup.key]: !prev[currentGroup.key],
    }))
  }

  return (
    <div className="repair-followup-card repair-followup-card--active">
      <div className="repair-followup-header">
        <div className="repair-followup-kicker">
          <span>{safeStepIndex + 1} / {totalSteps}</span>
        </div>
      </div>

      <div className="repair-followup-progress" aria-hidden="true">
        <span style={{ width: `${progressPercent}%` }} />
      </div>

      <section className="repair-followup-step-card">
        <div className="repair-followup-step-header">
          <div className="repair-followup-step-title-row">
            <div className="repair-followup-step-title">{currentGroup.label}</div>
            <span className={`repair-followup-group-level repair-followup-group-level--${currentGroup.requiredLevel}`}>
              {getRequiredLabel(currentGroup.requiredLevel)}
            </span>
          </div>
          {currentGroup.hint && (
            <div className="repair-followup-group-hint">{currentGroup.hint}</div>
          )}
        </div>

        {currentGroup.presets.length > 0 && (
          <div className="repair-followup-choice-list">
            {currentGroup.presets.map((preset) => {
              const active = currentGroup.selectedPresets.includes(preset)
              return (
                <button
                  key={preset}
                  type="button"
                  className={`repair-followup-choice ${active ? 'is-active' : ''}`}
                  onClick={() => onTogglePreset(currentGroup.key, preset)}
                  disabled={disabled}
                  aria-pressed={active}
                >
                  <span className="repair-followup-choice-text">{preset}</span>
                  <span className="repair-followup-choice-indicator">{active ? '已选' : '选择'}</span>
                </button>
              )
            })}
          </div>
        )}

        {hasPresetChoices && !forceManualInput && (
          <button
            type="button"
            className={`repair-followup-manual-trigger ${showManualInput ? 'is-open' : ''}`}
            onClick={toggleManualInput}
            disabled={disabled}
          >
            {showManualInput ? '收起手动补充' : '没有合适项，我来补充'}
          </button>
        )}

        {forceManualInput && (
          <div className="repair-followup-group-hint">
            候选里没有您的报码时，直接在下方补充具体报码编号；如果暂时还没读取报码，也可以先选择“暂未读取到具体报码”。
          </div>
        )}

        {showManualInput && (
          <textarea
            className="repair-followup-textarea"
            value={currentGroup.textValue}
            onChange={(event) => onTextChange(currentGroup.key, event.target.value)}
            placeholder={currentGroup.placeholder || `补充${currentGroup.label}`}
            rows={3}
            disabled={disabled}
          />
        )}

        {!currentAnswered && currentGroup.requiredLevel === 'hard' && (
          <div className="repair-followup-step-warning">
            这一项是当前判断必需信息，请至少选择一项或手动补充。
          </div>
        )}
      </section>

      <div className="repair-followup-actions">
        <button
          type="button"
          className="repair-followup-nav repair-followup-nav--secondary"
          onClick={handleBack}
          disabled={disabled || safeStepIndex === 0}
        >
          <ChevronLeft size={15} />
          <span>上一步</span>
        </button>

        <button
          type="button"
          className="repair-followup-nav repair-followup-nav--primary"
          onClick={handleNext}
          disabled={primaryDisabled}
        >
          {isLastStep ? (
            <>
              <Send size={15} />
              <span>提交补充信息</span>
            </>
          ) : (
            <>
              <span>{!currentAnswered && currentGroup.requiredLevel !== 'hard' ? '跳过此项' : '下一步'}</span>
              <ChevronRight size={15} />
            </>
          )}
        </button>
      </div>

      {state.quickActions.length > 0 && (
        <div className="repair-followup-quick-actions">
          {state.quickActions.map((action) => (
            <button
              key={action.key}
              type="button"
              className="repair-followup-quick-action"
              onClick={() => onQuickAction(action.key)}
              disabled={disabled}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

      {displaySummary && isLastStep && (
        <div className="repair-followup-final-hint">
          提交后将基于以上补充继续分析。
        </div>
      )}
    </div>
  )
}
