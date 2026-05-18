/**
 * SwitchConfirmDialog - 上下文切换确认对话框
 *
 * 当用户在进行中的对话中输入新话题时，显示此对话框确认是否切换。
 */

import React from 'react'
import { Circle, CircleQuestionMark, Search, TriangleAlert, MessageSquare, Cpu, Info } from 'lucide-react'
import type { BusinessType } from '../types'

interface SwitchConfirmDialogProps {
  /** 是否显示 */
  isOpen: boolean
  /** 当前业务类型 */
  currentBusiness: BusinessType
  /** 当前业务的额外信息 */
  contextInfo?: {
    clarifyRound?: number
    query?: string
    businessDuration?: number
  }
  /** 确认切换 */
  onConfirm: () => void
  /** 取消切换 */
  onCancel: () => void
}

/** 业务类型名称映射 */
const BUSINESS_NAMES: Record<BusinessType, string> = {
  IDLE: '空闲',
  INTENT_CLARIFYING: '意图澄清',
  DOC_SEARCH: '资料搜索',
  PARAM_QUERY: '参数查询',
  FAULT_DIAGNOSIS: '故障诊断',
  GENERAL_CHAT: '维修问答',
  AGENT_LOOP: '维修问答'
}

/** 业务类型图标映射 */
const BUSINESS_ICONS: Record<BusinessType, React.ReactNode> = {
  IDLE: <Circle size={20} />,
  INTENT_CLARIFYING: <CircleQuestionMark size={20} />,
  DOC_SEARCH: <Search size={20} />,
  PARAM_QUERY: <Cpu size={20} />,
  FAULT_DIAGNOSIS: <TriangleAlert size={20} />,
  GENERAL_CHAT: <MessageSquare size={20} />,
  AGENT_LOOP: <MessageSquare size={20} />
}

const SwitchConfirmDialog: React.FC<SwitchConfirmDialogProps> = ({
  isOpen,
  currentBusiness,
  contextInfo,
  onConfirm,
  onCancel
}) => {
  if (!isOpen) return null

  const businessName = BUSINESS_NAMES[currentBusiness] || currentBusiness
  const businessIcon = BUSINESS_ICONS[currentBusiness]

  return (
    <>
      {/* 背景遮罩 */}
      <div
        className="switch-dialog-overlay"
        onClick={onCancel}
      />

      {/* 对话框 */}
      <div className="switch-dialog">
        {/* 警告图标 */}
        <div className="switch-dialog-icon">
          <Info size={32} />
        </div>

        {/* 标题 */}
        <h3 className="switch-dialog-title">检测到新问题</h3>

        {/* 当前状态信息 */}
        <div className="switch-dialog-current">
          <div className="switch-dialog-current-header">
            <span className="switch-dialog-current-icon">
              {businessIcon}
            </span>
            <span className="switch-dialog-current-label">当前进行中</span>
          </div>
          <div className="switch-dialog-current-name">{businessName}</div>

          {contextInfo && (
            <div className="switch-dialog-current-info">
              {contextInfo.query && (
                <span className="switch-dialog-info-item">
                  「{contextInfo.query.slice(0, 20)}{contextInfo.query.length > 20 ? '...' : ''}」
                </span>
              )}
              {contextInfo.clarifyRound !== undefined && contextInfo.clarifyRound > 0 && (
                <span className="switch-dialog-info-item">
                  已完成 {contextInfo.clarifyRound} 轮澄清
                </span>
              )}
            </div>
          )}
        </div>

        {/* 提示文字 */}
        <p className="switch-dialog-message">
          开始新对话将清除当前进度。
        </p>

        {/* 按钮区 */}
        <div className="switch-dialog-actions">
          <button
            className="switch-dialog-btn switch-dialog-btn-cancel"
            onClick={onCancel}
          >
            取消，继续当前
          </button>
          <button
            className="switch-dialog-btn switch-dialog-btn-confirm"
            onClick={onConfirm}
          >
            确定，开始新的
          </button>
        </div>
      </div>
    </>
  )
}

export default SwitchConfirmDialog
