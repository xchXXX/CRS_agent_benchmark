/**
 * ECU 选择卡片
 *
 * 单选列表，显示 ECU 型号及匹配故障码数量
 * 第一项（推荐）有视觉强调
 * 最后一项「手动输入」：点击展开输入框
 */

import { useState } from 'react'
import { Cpu, Check, X, Pencil, ArrowRight } from 'lucide-react'
import type { EcuSummaryItem } from '@/types'

interface EcuSelectionCardProps {
  ecuOptions: EcuSummaryItem[]
  selectedEcu: string | undefined
  onSelect: (ecuModel: string) => void
  onConfirm: () => void
  loading?: boolean
}

export default function EcuSelectionCard({
  ecuOptions,
  selectedEcu,
  onSelect,
  onConfirm,
  loading = false
}: EcuSelectionCardProps) {
  const [showManualInput, setShowManualInput] = useState(false)
  const [manualValue, setManualValue] = useState('')

  // 判断当前选中的ECU是否为手动输入（不在选项列表中）
  const isManualEcu = selectedEcu && !ecuOptions.some(ecu => ecu.ecu_model === selectedEcu)

  const handleManualSubmit = () => {
    if (manualValue.trim()) {
      onSelect(manualValue.trim().toUpperCase())
      setShowManualInput(false)
      setManualValue('')
    }
  }

  // 点击编辑按钮，进入编辑模式
  const handleEditManualEcu = () => {
    if (selectedEcu) {
      setManualValue(selectedEcu)
      setShowManualInput(true)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleManualSubmit()
    } else if (e.key === 'Escape') {
      setShowManualInput(false)
      setManualValue('')
    }
  }

  return (
    <div className="ecu-selection-card">
      <div className="ecu-selection-header">
        <div className="ecu-selection-icon">
          <Cpu size={20} />
        </div>
        <span className="ecu-selection-title">选择 ECU 型号</span>
      </div>

      <div className="ecu-option-list">
        {ecuOptions.map((ecu) => (
          <label
            key={ecu.ecu_model}
            className={`ecu-option-item ${selectedEcu === ecu.ecu_model ? 'selected' : ''} ${ecu.recommended ? 'recommended' : ''}`}
          >
            <span className="ecu-radio">
              <input
                type="radio"
                name="ecu-select"
                checked={selectedEcu === ecu.ecu_model}
                onChange={() => onSelect(ecu.ecu_model)}
              />
              <span className="radio-custom" />
            </span>
            <span className="ecu-info">
              <span className="ecu-model">{ecu.ecu_model}</span>
              <span className="ecu-match-count">
                匹配 <strong>{ecu.match_count}</strong> 个故障码
              </span>
            </span>
            {ecu.recommended && (
              <span className="ecu-recommended-badge">推荐</span>
            )}
          </label>
        ))}

        {/* 手动输入选项 */}
        {showManualInput ? (
          <div className="ecu-manual-input-container">
            <input
              type="text"
              className="ecu-manual-input"
              value={manualValue}
              onChange={(e) => setManualValue(e.target.value.toUpperCase())}
              onKeyDown={handleKeyDown}
              placeholder="输入ECU型号，如 EDC17CV44"
              autoFocus
            />
            <button
              type="button"
              className="ecu-manual-submit"
              onClick={handleManualSubmit}
              disabled={!manualValue.trim()}
            >
              <Check size={16} />
            </button>
            <button
              type="button"
              className="ecu-manual-cancel"
              onClick={() => {
                setShowManualInput(false)
                setManualValue('')
              }}
            >
              <X size={16} />
            </button>
          </div>
        ) : isManualEcu ? (
          /* 展示手动输入的ECU，带编辑按钮 */
          <div className="ecu-option-item selected manual-ecu-item">
            <span className="ecu-radio">
              <input
                type="radio"
                name="ecu-select"
                checked={true}
                readOnly
              />
              <span className="radio-custom" />
            </span>
            <span className="ecu-info">
              <span className="ecu-model">{selectedEcu}</span>
              <span className="ecu-match-count manual-label">手动输入</span>
            </span>
            <button
              type="button"
              className="ecu-edit-btn"
              onClick={handleEditManualEcu}
              title="编辑ECU型号"
            >
              <Pencil size={14} />
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="ecu-manual-btn"
            onClick={() => setShowManualInput(true)}
          >
            <span className="ecu-manual-icon">
              <Pencil size={16} />
            </span>
            <span className="ecu-manual-text">手动输入ECU型号</span>
          </button>
        )}
      </div>

      <button
        type="button"
        className="ecu-confirm-btn"
        onClick={onConfirm}
        disabled={!selectedEcu || loading}
      >
        {loading ? (
          <>
            <span className="btn-spinner" />
            查询中...
          </>
        ) : (
          <>
            确认诊断
            <ArrowRight size={18} />
          </>
        )}
      </button>
    </div>
  )
}
