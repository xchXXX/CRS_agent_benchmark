import { useState, useCallback } from 'react'
import { submitFeedback } from '../services/api'
import './FeedbackCard.css'

const STORAGE_KEY = 'feedback_submitted_ids'
const FEEDBACK_ENABLED = import.meta.env.VITE_ENABLE_FEEDBACK !== 'false'

function getSubmittedIds(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'))
  } catch { return new Set() }
}

function markSubmittedInStorage(requestId: string) {
  const ids = getSubmittedIds()
  ids.add(requestId)
  // 只保留最近 500 条，避免无限增长
  const arr = [...ids]
  localStorage.setItem(STORAGE_KEY, JSON.stringify(arr.slice(-500)))
}

export function isFeedbackSubmitted(requestId: string): boolean {
  return getSubmittedIds().has(requestId)
}

interface FeedbackCardProps {
  requestId: string
  sessionId: string | null
  businessType: string
  onSubmitted: () => void
}

const TAG_CONFIG: Record<string, { positive: string[]; negative: string[] }> = {
  DOC_SEARCH: {
    positive: ['结果准确', '响应快速', '文档齐全'],
    negative: ['结果不相关', '缺少文档', '搜索太慢'],
  },
  FAULT_DIAGNOSIS: {
    positive: ['诊断准确', '分析详细', '方案有效'],
    negative: ['诊断有误', '不够详细', '缺少方案'],
  },
  PARAM_QUERY: {
    positive: ['参数准确', '定位清晰', '结果实用'],
    negative: ['参数不准', '定位错误', '结果不全'],
  },
  GENERAL_CHAT: {
    positive: ['回答准确', '解释清晰', '内容全面'],
    negative: ['回答有误', '不够详细', '答非所问'],
  },
}

export default function FeedbackCard({ requestId, sessionId, businessType, onSubmitted }: FeedbackCardProps) {
  const [rating, setRating] = useState(0)
  const [hoverRating, setHoverRating] = useState(0)
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const displayRating = hoverRating || rating

  const getTags = useCallback(() => {
    if (!rating) return []
    const config = TAG_CONFIG[businessType] || TAG_CONFIG.GENERAL_CHAT
    if (rating >= 7) return config.positive
    if (rating <= 4) return config.negative
    return [...config.positive, ...config.negative]
  }, [rating, businessType])

  // 所有 hooks 之后再做条件判断
  if (!FEEDBACK_ENABLED || isFeedbackSubmitted(requestId)) return null

  const toggleTag = (tag: string) => {
    setSelectedTags(prev =>
      prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
    )
  }

  const handleSubmit = async () => {
    if (!rating || submitting) return
    setSubmitting(true)
    try {
      await submitFeedback({
        request_id: requestId,
        session_id: sessionId || '',
        rating,
        business_type: businessType,
        tags: selectedTags.length > 0 ? selectedTags : undefined,
        comment: comment.trim() || undefined,
      })
      setSubmitted(true)
      // 先展示感谢动画，延迟后再持久化并通知父组件
      setTimeout(() => {
        markSubmittedInStorage(requestId)
        onSubmitted()
      }, 1500)
    } catch (e) {
      console.warn('反馈提交失败:', e)
      setSubmitting(false)
    }
  }

  if (submitted) {
    return (
      <div className="feedback-card feedback-thankyou">
        <span className="feedback-thankyou-icon">&#10003;</span>
        <span>感谢反馈</span>
      </div>
    )
  }

  const tags = getTags()

  return (
    <div className="feedback-card">
      <div className="feedback-header">
        <span className="feedback-label">这个回答有帮助吗？</span>
      </div>

      {/* 星级评分 */}
      <div className="feedback-stars" onMouseLeave={() => setHoverRating(0)}>
        {[1, 2, 3, 4, 5].map(star => {
          const leftVal = star * 2 - 1
          const rightVal = star * 2
          const filled = displayRating >= rightVal
          const halfFilled = !filled && displayRating >= leftVal
          return (
            <span key={star} className="feedback-star-wrapper">
              <svg viewBox="0 0 24 24" className="feedback-star-svg">
                <defs>
                  <linearGradient id={`star-grad-${requestId}-${star}`}>
                    <stop offset="50%" stopColor={halfFilled || filled ? 'var(--accent-amber)' : 'var(--border-color)'} />
                    <stop offset="50%" stopColor={filled ? 'var(--accent-amber)' : 'var(--border-color)'} />
                  </linearGradient>
                </defs>
                <path
                  d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"
                  fill={`url(#star-grad-${requestId}-${star})`}
                  stroke={displayRating >= leftVal ? 'var(--accent-amber)' : 'var(--border-color)'}
                  strokeWidth="1"
                  strokeLinejoin="round"
                />
              </svg>
              <span
                className="feedback-star-zone feedback-star-left"
                onMouseEnter={() => setHoverRating(leftVal)}
                onClick={() => setRating(leftVal)}
              />
              <span
                className="feedback-star-zone feedback-star-right"
                onMouseEnter={() => setHoverRating(rightVal)}
                onClick={() => setRating(rightVal)}
              />
            </span>
          )
        })}
        {displayRating > 0 && (
          <span className="feedback-rating-text">{(displayRating / 2).toFixed(1)}</span>
        )}
      </div>

      {/* 快捷标签 */}
      {tags.length > 0 && (
        <div className="feedback-tags">
          {tags.map(tag => (
            <button
              key={tag}
              className={`feedback-tag ${selectedTags.includes(tag) ? 'feedback-tag-active' : ''}`}
              onClick={() => toggleTag(tag)}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* 文本输入 */}
      {rating > 0 && (
        <div className="feedback-comment-wrap">
          <textarea
            className="feedback-comment"
            placeholder="有其他想说的？（选填）"
            value={comment}
            onChange={e => setComment(e.target.value.slice(0, 500))}
            rows={2}
            maxLength={500}
          />
          {comment.length > 0 && (
            <span className="feedback-char-count">{comment.length}/500</span>
          )}
        </div>
      )}

      {/* 提交按钮 */}
      {rating > 0 && (
        <button
          className="feedback-submit"
          onClick={handleSubmit}
          disabled={submitting}
        >
          {submitting ? '提交中...' : '提交反馈'}
        </button>
      )}
    </div>
  )
}
