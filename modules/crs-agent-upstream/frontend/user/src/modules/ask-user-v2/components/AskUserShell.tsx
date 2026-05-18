import { Archive, CircleCheckBig } from 'lucide-react'

import type { AskUserV2FormState, AskUserV2Submission } from '../types'
import AskUserFormV2 from './AskUserFormV2'

interface AskUserShellProps {
  state: AskUserV2FormState
  isLoading?: boolean
  onSubmit: (submission: AskUserV2Submission) => Promise<boolean> | boolean
}

export default function AskUserShell({
  state,
  isLoading = false,
  onSubmit,
}: AskUserShellProps) {
  const visibleFieldCount = state.form.sections.reduce((count, section) => count + section.fields.length, 0)
  const isRepairScene = state.scene === 'repair_knowledge_followup'
  const useWizardAppearance = visibleFieldCount === 1
  const useCompactPanel = Boolean(
    visibleFieldCount === 1
    || state.form.mode === 'progressive'
    || state.form.ui_policy?.layout === 'stepper'
  )
  const submittedTitle = isRepairScene ? '已补充维修信息' : '已补充关键信息'
  const submittingTitle = isRepairScene ? '已提交维修信息，正在分析' : '已提交补充信息，正在分析'

  if (state.status === 'archived') {
    return (
      <div className="ask-user-v2-shell ask-user-v2-shell--archived">
        <Archive size={14} className="ask-user-v2-shell-icon ask-user-v2-shell-icon--archived" />
        <div className="ask-user-v2-shell-copy">
          <div className="ask-user-v2-shell-title">补充信息已归档</div>
          <div className="ask-user-v2-shell-text">{state.originalQuery || state.question}</div>
        </div>
      </div>
    )
  }

  if (state.status === 'submitted') {
    return (
      <div className="ask-user-v2-shell ask-user-v2-shell--submitted">
        <CircleCheckBig size={18} className="ask-user-v2-shell-icon ask-user-v2-shell-icon--submitted" />
        <div className="ask-user-v2-shell-copy">
          <div className="ask-user-v2-shell-title">{submittedTitle}</div>
          <div className="ask-user-v2-shell-text">{state.summaryText || state.question}</div>
        </div>
      </div>
    )
  }

  if (state.status === 'submitting') {
    return (
      <div className="ask-user-v2-shell ask-user-v2-shell--submitted">
        <CircleCheckBig size={18} className="ask-user-v2-shell-icon ask-user-v2-shell-icon--submitted" />
        <div className="ask-user-v2-shell-copy">
          <div className="ask-user-v2-shell-title">{submittingTitle}</div>
          <div className="ask-user-v2-shell-text">{state.summaryText || state.question}</div>
        </div>
      </div>
    )
  }

  return (
    <div
      className={`ask-user-v2-shell ask-user-v2-shell--active ${useWizardAppearance ? 'ask-user-v2-shell--wizard' : ''} ${useCompactPanel ? 'ask-user-v2-shell--compact-panel' : ''}`}
    >
      {state.summaryText && !useCompactPanel ? (
        <div className="ask-user-v2-shell-history">
          <div className="ask-user-v2-shell-history-title">{submittedTitle}</div>
          <div className="ask-user-v2-shell-history-text">{state.summaryText}</div>
        </div>
      ) : null}
      <AskUserFormV2
        question={state.question}
        form={state.form}
        compactPanel={useCompactPanel}
        disabled={isLoading}
        onSubmit={onSubmit}
      />
    </div>
  )
}
