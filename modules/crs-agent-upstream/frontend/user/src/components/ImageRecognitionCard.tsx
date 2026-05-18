/**
 * 图片故障码识别卡片
 *
 * 状态1：识别中 - 显示图片缩略图 + 扫描动画
 * 状态2：识别结果 - 故障码多选列表（checkbox），默认全选，支持手动添加
 * 状态3：识别失败 - 错误信息 + 重试按钮
 */

import { useState } from 'react'
import { X, Check, Plus, ArrowRight, CircleX, RefreshCw } from 'lucide-react'
import type { RecognizedFaultCode } from '@/types'

// 组件状态类型
export type RecognitionStatus = 'recognizing' | 'success' | 'failed'

interface ImageRecognitionCardProps {
  status: RecognitionStatus
  imagePreview?: string
  faultCodes: RecognizedFaultCode[]
  selectedCodes: string[]
  onSelectionChange: (codes: string[]) => void
  onAddCode: (code: RecognizedFaultCode) => void
  onRemoveCode: (normalized: string) => void  // 新增：删除故障码回调
  onNext: () => void
  onRetry: () => void
  errorMessage?: string
}

export default function ImageRecognitionCard({
  status,
  imagePreview,
  faultCodes,
  selectedCodes,
  onSelectionChange,
  onAddCode,
  onRemoveCode,
  onNext,
  onRetry,
  errorMessage
}: ImageRecognitionCardProps) {
  // 手动添加状态
  const [showAddInput, setShowAddInput] = useState(false)
  const [manualCode, setManualCode] = useState('')

  // 切换单个故障码选中状态
  const toggleCode = (normalized: string) => {
    if (selectedCodes.includes(normalized)) {
      onSelectionChange(selectedCodes.filter(c => c !== normalized))
    } else {
      onSelectionChange([...selectedCodes, normalized])
    }
  }

  // 全选/取消全选
  const toggleAll = () => {
    if (selectedCodes.length === faultCodes.length) {
      onSelectionChange([])
    } else {
      onSelectionChange(faultCodes.map(fc => fc.normalized))
    }
  }

  // 处理手动添加故障码
  const handleAddManualCode = () => {
    const code = manualCode.trim().toUpperCase()
    if (!code) return

    // 检查是否已存在
    if (faultCodes.some(fc => fc.normalized === code)) {
      // 如果已存在但未选中，则选中它
      if (!selectedCodes.includes(code)) {
        onSelectionChange([...selectedCodes, code])
      }
      setManualCode('')
      setShowAddInput(false)
      return
    }

    // 创建新的故障码对象
    const newCode: RecognizedFaultCode = {
      raw: code,
      normalized: code,
      type: 'MANUAL',
      description: '手动添加',
      status: null,
      selected: true
    }

    onAddCode(newCode)
    // 自动选中新添加的故障码
    onSelectionChange([...selectedCodes, code])
    setManualCode('')
    setShowAddInput(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleAddManualCode()
    } else if (e.key === 'Escape') {
      setShowAddInput(false)
      setManualCode('')
    }
  }

  return (
    <div className="image-recognition-card">
      {/* 图片预览区 */}
      {imagePreview && (
        <div className="recognition-image-container">
          <img src={imagePreview} alt="诊断图片" className="recognition-image" />
          {status === 'recognizing' && (
            <div className="recognition-scan-overlay">
              <div className="scan-line" />
            </div>
          )}
        </div>
      )}

      {/* 识别中状态 */}
      {status === 'recognizing' && (
        <div className="recognition-loading">
          <div className="recognition-loading-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" className="scan-circle" />
              <path d="M12 2a10 10 0 0 1 10 10" className="scan-arc" />
            </svg>
          </div>
          <span className="recognition-loading-text">正在识别故障码...</span>
        </div>
      )}

      {/* 识别成功状态 */}
      {status === 'success' && (
        <>
          <div className="recognition-header">
            <div className="recognition-count">
              <span className="count-number">{faultCodes.length}</span>
              <span className="count-label">个故障码</span>
            </div>
            <button
              type="button"
              className="recognition-select-all"
              onClick={toggleAll}
            >
              {selectedCodes.length === faultCodes.length ? '取消全选' : '全选'}
            </button>
          </div>

          <div className="recognition-code-list">
            {faultCodes.map((fc) => (
              <label
                key={fc.normalized}
                className={`recognition-code-item ${selectedCodes.includes(fc.normalized) ? 'selected' : ''}`}
              >
                <span className="code-checkbox">
                  <input
                    type="checkbox"
                    checked={selectedCodes.includes(fc.normalized)}
                    onChange={() => toggleCode(fc.normalized)}
                  />
                  <span className="checkbox-custom">
                    <Check size={14} strokeWidth={3} />
                  </span>
                </span>
                <span className="code-info">
                  <span className="code-normalized">{fc.normalized}</span>
                  <span className="code-description">{fc.description || '暂无描述'}</span>
                </span>
                {fc.status && (
                  <span className={`code-status status-${fc.status === '当前' ? 'current' : 'history'}`}>
                    {fc.status}
                  </span>
                )}
                {fc.type === 'MANUAL' && (
                  <>
                    <span className="code-status status-manual">手动</span>
                    <button
                      type="button"
                      className="code-delete-btn"
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        // 从选中列表中移除
                        if (selectedCodes.includes(fc.normalized)) {
                          onSelectionChange(selectedCodes.filter(c => c !== fc.normalized))
                        }
                        // 从故障码列表中移除
                        onRemoveCode(fc.normalized)
                      }}
                      title="删除"
                    >
                      <X size={14} />
                    </button>
                  </>
                )}
              </label>
            ))}

            {/* 手动添加按钮/输入框 */}
            {showAddInput ? (
              <div className="manual-add-input-container">
                <input
                  type="text"
                  className="manual-add-input"
                  value={manualCode}
                  onChange={(e) => setManualCode(e.target.value.toUpperCase())}
                  onKeyDown={handleKeyDown}
                  placeholder="输入故障码，如 P0171"
                  autoFocus
                />
                <button
                  type="button"
                  className="manual-add-submit"
                  onClick={handleAddManualCode}
                  disabled={!manualCode.trim()}
                >
                  <Check size={16} />
                </button>
                <button
                  type="button"
                  className="manual-add-cancel"
                  onClick={() => {
                    setShowAddInput(false)
                    setManualCode('')
                  }}
                >
                  <X size={16} />
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="manual-add-btn"
                onClick={() => setShowAddInput(true)}
              >
                <span className="manual-add-icon">
                  <Plus size={16} />
                </span>
                <span className="manual-add-text">手动添加故障码</span>
              </button>
            )}
          </div>

          <button
            type="button"
            className="recognition-next-btn"
            onClick={onNext}
            disabled={selectedCodes.length === 0}
          >
            <span>下一步</span>
            <ArrowRight size={18} />
          </button>
        </>
      )}

      {/* 无故障码但可手动添加 */}
      {status === 'success' && faultCodes.length === 0 && !showAddInput && (
        <div className="recognition-empty">
          <div className="recognition-empty-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <path d="M16 16s-1.5-2-4-2-4 2-4 2" />
              <line x1="9" y1="9" x2="9.01" y2="9" />
              <line x1="15" y1="9" x2="15.01" y2="9" />
            </svg>
          </div>
          <span className="recognition-empty-text">未识别到故障码</span>
          <span className="recognition-empty-hint">您可以手动添加或重新上传图片</span>
          <div className="recognition-empty-actions">
            <button
              type="button"
              className="recognition-manual-btn"
              onClick={() => setShowAddInput(true)}
            >
              <Plus size={16} />
              手动添加
            </button>
            <button type="button" className="recognition-retry-btn" onClick={onRetry}>
              重新上传
            </button>
          </div>
        </div>
      )}

      {/* 识别失败状态 */}
      {status === 'failed' && (
        <div className="recognition-failed">
          <div className="recognition-failed-icon">
            <CircleX size={32} />
          </div>
          <span className="recognition-failed-text">{errorMessage || '识别失败'}</span>
          <button type="button" className="recognition-retry-btn" onClick={onRetry}>
            <RefreshCw size={14} />
            重试
          </button>
        </div>
      )}
    </div>
  )
}
