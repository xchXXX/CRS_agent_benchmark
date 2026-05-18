import React from 'react'
import { TriangleAlert, Search, MessageSquare, Cpu, ChevronRight, CircleQuestionMark } from 'lucide-react'
import type { SuggestedQuestion } from '../types'

interface SuggestionChipsProps {
  suggestions: SuggestedQuestion[]
  onSelect: (suggestion: SuggestedQuestion) => void
  disabled?: boolean
}

/**
 * 推荐问题芯片组件
 * 显示在助手回复下方，用户点击可快速发送预设问题
 */
const SuggestionChips: React.FC<SuggestionChipsProps> = ({
  suggestions,
  onSelect,
  disabled = false
}) => {
  if (!suggestions || suggestions.length === 0) {
    return null
  }

  // 过滤掉 action_type 为 none 且 query 为空的
  const validSuggestions = suggestions.filter(
    s => s.action_type !== 'none' || s.query
  )

  if (validSuggestions.length === 0) {
    return null
  }

  // 根据 action_type 获取图标
  const getIcon = (actionType: SuggestedQuestion['action_type']) => {
    switch (actionType) {
      case 'fault_diagnosis':
        return <TriangleAlert size={14} />
      case 'doc_search':
        return <Search size={14} />
      case 'general_chat':
        return <MessageSquare size={14} />
      case 'param_query':
        return <Cpu size={14} />
      default:
        return <ChevronRight size={14} />
    }
  }

  return (
    <div className="suggestion-chips-container">
      <div className="suggestion-chips-label">
        <CircleQuestionMark size={16} />
        <span>您可能想问</span>
      </div>
      <div className="suggestion-chips">
        {validSuggestions.map((suggestion, index) => (
          <button
            key={`${suggestion.text}-${index}`}
            className={`suggestion-chip suggestion-chip-${suggestion.action_type}`}
            onClick={() => onSelect(suggestion)}
            disabled={disabled || suggestion.action_type === 'none'}
            style={{ animationDelay: `${index * 0.1}s` }}
          >
            <span className="suggestion-chip-icon">
              {getIcon(suggestion.action_type)}
            </span>
            <span className="suggestion-chip-text">{suggestion.text}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default SuggestionChips
