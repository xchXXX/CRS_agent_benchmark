import api from './api'
import type { LogDetail, LogItem } from './logs'

export interface FeedbackRunSummary {
  run_id: string
  request_id: string
  sequence_no: number
  trigger_type: string | null
  run_status: string
  end_reason: string | null
  response_type: string | null
  response_preview: string | null
  ask_user_question: string | null
  missing_fields: string[]
  tool_call_count: number
  external_tool_call_count: number
  elapsed_ms: number | null
  created_at: string | null
}

export interface FeedbackItem {
  id: number
  request_id: string
  session_id: string
  rating: number
  business_type: string
  tags: string[] | null
  comment: string | null
  created_at: string
  task_log: LogItem | null
  run_log: FeedbackRunSummary | null
  chat_log: {
    user_message: string
    response_type: string
    response_preview: string | null
    elapsed_ms: number | null
  } | null
}

export interface FeedbackDetail {
  id: number
  request_id: string
  session_id: string
  rating: number
  business_type: string
  tags: string[] | null
  comment: string | null
  created_at: string
  task_log: LogDetail | null
  run_log: FeedbackRunSummary | null
  chat_log: {
    id: number
    request_id: string
    session_id: string
    user_message: string
    client_type: string
    request_mode: string
    intent_type: string | null
    intent_confidence: number | null
    response_type: string
    response_content: string | Record<string, any> | null
    response_preview: string | null
    elapsed_ms: number | null
    report_url: string | null
    created_at: string
  } | null
}

export interface FeedbackListParams {
  page: number
  page_size: number
  business_type?: string
  rating_min?: number
  rating_max?: number
  start_time?: string
  end_time?: string
  has_comment?: boolean
}

export interface FeedbackListResponse {
  total: number
  page: number
  page_size: number
  items: FeedbackItem[]
}

export interface FeedbackStats {
  total_count: number
  avg_rating: number | null
  rating_distribution: Array<{ rating: number; count: number }>
  business_stats: Array<{ business_type: string; count: number; avg_rating: number | null }>
  top_tags: Array<{ tag: string; count: number }>
}

export const feedbackService = {
  async getList(params: FeedbackListParams) {
    return api.get<FeedbackListResponse>('/admin/feedback/list', { params })
  },

  async getDetail(id: number) {
    return api.get<FeedbackDetail>(`/admin/feedback/${id}`)
  },

  async getStats(days: number = 30) {
    return api.get<FeedbackStats>('/admin/feedback/stats', { params: { days } })
  },
}
