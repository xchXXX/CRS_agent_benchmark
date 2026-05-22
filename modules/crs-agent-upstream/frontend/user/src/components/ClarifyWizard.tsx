/**
 * 折叠式澄清向导组件
 *
 * 用单个组件替代多个 clarify_business 卡片，解决多轮澄清混乱问题。
 * - 面包屑展示已完成轮次（可点×回退）
 * - 当前轮次展示完整选项
 * - 三种状态：active / completed / archived
 * - 快捷入口：显示 Top1 结果，用户可直接短路查询
 */

import { useEffect, useState } from 'react'
import { Archive, CircleCheckBig, X, Info, Star, FileText, Eye, Check, ChevronRight } from 'lucide-react'

// 存在性信息类型
export interface ExistenceInfo {
  status: 'exact_match' | 'partial_match' | 'no_match'
  message?: string
  suggestions?: Record<string, string[]>
}

// Top1 结果类型（快捷入口数据）
export interface TopResult {
  file_id: string
  title: string
  score: number
  pic_folder_url: string
  brand?: string
  series?: string
  model?: string
  ggzj_sn?: number
  ggzj_data_type?: number
  ggzj_file_no?: string | null
  ggzj_file_type?: string | null
  selectionPayload?: Record<string, any>
}

// 单轮澄清数据
export interface WizardRound {
  id: string
  facet: string
  question: string
  toolCallId?: string
  inputType?: 'single_select' | 'multi_select' | 'number' | 'text'
  allowFreeInput?: boolean
  inputHint?: string
  unit?: string
  referenceRange?: string
  context?: Record<string, any>
  options: Array<{ key: string; label: string; description?: string; selectionPayload?: Record<string, any> }>
  selected?: string
  selectedLabel?: string
}

// 向导整体状态
export interface WizardState {
  rounds: WizardRound[]
  currentRoundIndex: number
  status: 'active' | 'completed' | 'archived'
  originalQuery: string
  resultsCount?: number
  topResult?: TopResult  // 快捷入口：Top1 结果
  existenceInfo?: ExistenceInfo  // 存在性验证信息
}

interface ClarifyWizardProps {
  state: WizardState
  onSelect: (choice: string, facet: string) => void
  onBack: (roundIndex: number) => void
  onExpand?: () => void
  isLoading?: boolean
  // ECU 输入模式支持
  ecuInputMode?: boolean
  onEcuInputModeChange?: (active: boolean) => void
  ecuInputValue?: string
  onEcuInputValueChange?: (value: string) => void
  // 快捷入口回调
  onQuickAccess?: (topResult: TopResult) => void    // 预览（只打开文档查看器）
  onQuickConfirm?: (topResult: TopResult) => void   // 确认（通知后端结束澄清）
}

export default function ClarifyWizard({
  state,
  onSelect,
  onBack,
  onExpand,
  isLoading = false,
  ecuInputMode = false,
  onEcuInputModeChange,
  ecuInputValue = '',
  onEcuInputValueChange,
  onQuickAccess,
  onQuickConfirm
}: ClarifyWizardProps) {
  const { rounds, currentRoundIndex, status, originalQuery, resultsCount, topResult, existenceInfo } = state
  const [freeInputValue, setFreeInputValue] = useState('')

  // 当前轮次
  const currentRound = rounds[currentRoundIndex]
  // 已完成的轮次（面包屑）
  const completedRounds = rounds.slice(0, currentRoundIndex)
  const useOptionsAsSuggestions = Boolean(
    currentRound?.allowFreeInput && currentRound?.inputType === 'text'
  )

  // 处理回退
  const handleBack = (index: number) => {
    if (isLoading) return
    onBack(index)
  }

  // 处理选择
  const handleSelect = (choice: string, facet: string) => {
    if (isLoading) return
    onSelect(choice, facet)
  }

  useEffect(() => {
    setFreeInputValue('')
  }, [currentRound?.id])

  // ECU 输入提交
  const handleEcuSubmit = () => {
    if (ecuInputValue.trim() && currentRound) {
      onEcuInputModeChange?.(false)
      handleSelect(ecuInputValue.trim(), currentRound.facet)
    }
  }

  const handleFreeInputSubmit = () => {
    if (!currentRound || !freeInputValue.trim()) return
    handleSelect(freeInputValue.trim(), currentRound.facet)
  }

  // 归档状态：折叠显示摘要
  if (status === 'archived') {
    return (
      <div className="clarify-wizard clarify-wizard--archived">
        <div className="wizard-archived-summary">
          <Archive size={16} className="wizard-archived-icon" />
          <span className="wizard-archived-text">
            已归档：{originalQuery}
            {completedRounds.length > 0 && (
              <span className="wizard-archived-filters">
                （{completedRounds.map(r => r.selectedLabel || r.selected).join(' → ')}）
              </span>
            )}
          </span>
        </div>
      </div>
    )
  }

  // 完成状态：展示摘要，并允许从最终结果回退到任一已选步骤
  if (status === 'completed') {
    return (
      <div className="clarify-wizard clarify-wizard--completed" onClick={onExpand}>
        <div className="wizard-completed-summary">
          <CircleCheckBig size={18} className="wizard-completed-icon" />
          <div className="wizard-completed-content">
            <span className="wizard-completed-query">{originalQuery}</span>
            {rounds.length > 0 && (
              <div className="wizard-completed-path-list">
                {rounds.map((round, idx) => (
                  <div key={round.id} className="wizard-completed-step">
                    {idx > 0 && <span className="wizard-breadcrumb-arrow">→</span>}
                    <span className="wizard-breadcrumb-value">{round.selectedLabel || round.selected}</span>
                    <button
                      type="button"
                      className="wizard-breadcrumb-remove"
                      onClick={(event) => {
                        event.stopPropagation()
                        handleBack(idx)
                      }}
                      disabled={isLoading}
                      title="回退到此步骤"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          {resultsCount !== undefined && (
            <span className="wizard-completed-count">{resultsCount} 个结果</span>
          )}
        </div>
      </div>
    )
  }

  // 活跃状态：完整向导 UI
  return (
    <div className="clarify-wizard clarify-wizard--active">
      {/* 面包屑：已完成的轮次 */}
      {completedRounds.length > 0 && (
        <div className="wizard-breadcrumb">
          <span className="wizard-breadcrumb-query">{originalQuery}</span>
          {completedRounds.map((round, idx) => (
            <div key={round.id} className="wizard-breadcrumb-item">
              <span className="wizard-breadcrumb-arrow">→</span>
              <span className="wizard-breadcrumb-value">{round.selectedLabel || round.selected}</span>
              <button
                type="button"
                className="wizard-breadcrumb-remove"
                onClick={() => handleBack(idx)}
                disabled={isLoading}
                title="回退到此步骤"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 存在性提示 - partial_match 时显示 */}
      {existenceInfo?.status === 'partial_match' && existenceInfo.message && (
        <div className="existence-hint existence-hint--warning">
          <Info size={16} className="existence-hint-icon" />
          <span className="existence-hint-text">{existenceInfo.message}</span>
        </div>
      )}

      {/* 快捷入口 - Top1 结果 */}
      {topResult && (
        <div className="wizard-quick-access">
          <div className="quick-access-label">
            <Star size={14} />
            <span>当前为您找到最佳匹配文档：</span>
          </div>
          <div
            className="quick-access-card"
            style={{ opacity: isLoading ? 0.6 : 1 }}
          >
            {/* 主体区域 - 点击预览文档 */}
            <div
              className="quick-access-main"
              onClick={() => !isLoading && onQuickAccess?.(topResult)}
              style={{ cursor: isLoading ? 'not-allowed' : 'pointer' }}
            >
              <div className="quick-access-icon">
                <FileText size={24} />
              </div>
              <div className="quick-access-content">
                <div className="quick-access-title">{topResult.title}</div>
                {(topResult.brand || topResult.series || topResult.model) && (
                  <div className="quick-access-tags">
                    {topResult.brand && <span className="quick-access-tag">{topResult.brand}</span>}
                    {topResult.series && <span className="quick-access-tag">{topResult.series}</span>}
                    {topResult.model && <span className="quick-access-tag">{topResult.model}</span>}
                  </div>
                )}
                <div className="quick-access-hint">
                  <Eye size={14} />
                  <span>点击预览</span>
                </div>
              </div>
              <div className="quick-access-score">
                <span className="score-value">{(topResult.score * 100).toFixed(0)}</span>
                <span className="score-label">匹配度</span>
              </div>
            </div>

            {/* 确认按钮 - 点击确认选择 */}
            <button
              className="quick-access-confirm"
              onClick={() => !isLoading && onQuickConfirm?.(topResult)}
              disabled={isLoading}
            >
              <Check size={16} />
              <span>就是这个</span>
            </button>
          </div>
          <div className="wizard-divider">
            <span>不是想要的？请继续选择</span>
          </div>
        </div>
      )}

      {/* 当前轮次 */}
      {currentRound && (
        <div className="wizard-current-round">
          <div className="wizard-header">
            <div className="wizard-header-content">
              <span className="wizard-question">
                {/* 去掉"找到 xx 个相关结果。"前缀，只保留问题部分 */}
                {currentRound.question.replace(/^找到\s*\d+\s*个相关结果[。．.]\s*/i, '')}
              </span>
              {resultsCount !== undefined && (
                <span className="wizard-results-hint">当前 {resultsCount} 个结果</span>
              )}
            </div>
          </div>

          <div className="wizard-options">
            {currentRound.options.map((option, idx) => {
              // ECU 澄清时，"other" 选项特殊处理
              const isEcuOther = currentRound.facet === 'ecu' && option.key === 'other'

              if (isEcuOther && ecuInputMode) {
                // 显示输入框模式
                return (
                  <div key={option.key} className="ecu-input-container" style={{ animationDelay: `${idx * 0.08}s` }}>
                    <input
                      type="text"
                      className="ecu-input"
                      value={ecuInputValue}
                      onChange={(e) => onEcuInputValueChange?.(e.target.value.toUpperCase())}
                      placeholder="请输入ECU型号，如 EDC17CV44"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && ecuInputValue.trim()) {
                          handleEcuSubmit()
                        } else if (e.key === 'Escape') {
                          onEcuInputModeChange?.(false)
                          onEcuInputValueChange?.('')
                        }
                      }}
                    />
                    <button
                      className="ecu-submit-btn"
                      onClick={handleEcuSubmit}
                      disabled={!ecuInputValue.trim() || isLoading}
                    >
                      <Check size={16} />
                    </button>
                    <button
                      className="ecu-cancel-btn"
                      onClick={() => {
                        onEcuInputModeChange?.(false)
                        onEcuInputValueChange?.('')
                      }}
                    >
                      <X size={16} />
                    </button>
                  </div>
                )
              }

              return (
                <button
                  key={option.key}
                  className={`wizard-option ${isEcuOther ? 'wizard-option-other' : ''} ${option.description ? 'wizard-option-with-desc' : ''}`}
                  onClick={() => {
                    if (isEcuOther) {
                      onEcuInputModeChange?.(true)
                      onEcuInputValueChange?.('')
                    } else {
                      if (useOptionsAsSuggestions) {
                        setFreeInputValue(option.label)
                      } else {
                        handleSelect(option.key, currentRound.facet)
                      }
                    }
                  }}
                  disabled={isLoading}
                  style={{ animationDelay: `${idx * 0.08}s` }}
                >
                  <span className="option-index">{isEcuOther ? '✎' : idx + 1}</span>
                  <div className="option-content">
                    <span className="option-text">{isEcuOther ? '输入ECU型号' : option.label}</span>
                    {option.description && (
                      <span className="option-desc">{option.description}</span>
                    )}
                  </div>
                  <ChevronRight size={16} className="option-arrow" />
                </button>
              )
            })}
          </div>

          {(currentRound.inputType === 'text' || currentRound.inputType === 'number' || currentRound.allowFreeInput) && (
            <div className="wizard-free-input">
              <div className="wizard-free-input-box">
                <input
                  type={currentRound.inputType === 'number' ? 'number' : 'text'}
                  className="ecu-input wizard-free-input-control"
                  value={freeInputValue}
                  onChange={(e) => setFreeInputValue(e.target.value)}
                  placeholder={currentRound.inputHint || '请输入补充信息'}
                  disabled={isLoading}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && freeInputValue.trim()) {
                      handleFreeInputSubmit()
                    }
                  }}
                />
                <button
                  className="ecu-submit-btn"
                  onClick={handleFreeInputSubmit}
                  disabled={!freeInputValue.trim() || isLoading}
                >
                  <Check size={16} />
                </button>
              </div>
              {(currentRound.referenceRange || currentRound.unit) && (
                <div className="wizard-free-input-hint">
                  {currentRound.referenceRange && <span>参考范围：{currentRound.referenceRange}</span>}
                  {currentRound.unit && <span>单位：{currentRound.unit}</span>}
                </div>
              )}
              {useOptionsAsSuggestions && currentRound.options.length > 0 && (
                <div className="wizard-free-input-hint">
                  <span>可先点上方建议项，再补充细节后发送。</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
