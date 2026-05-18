import api from './api'

export interface LogItem {
  id: number
  task_id: string
  session_id: string
  user_id: string | null
  client_type: string | null
  root_question: string
  latest_user_message: string | null
  business_type: string | null
  task_status: string
  end_reason: string | null
  convergence_mode: string | null
  final_response_type: string | null
  final_response_preview: string | null
  latest_ask_user_question: string | null
  latest_missing_fields: string[]
  ask_user_triggered: boolean
  ask_user_count: number
  run_count: number
  tool_call_count: number
  external_tool_call_count: number
  main_tool_names: string[]
  has_error: boolean
  error_type: string | null
  total_elapsed_ms: number | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface RunEventDetail {
  id: number
  event_id: string
  sequence_no: number
  event_type: string
  phase: string | null
  tool_name: string | null
  summary: string | null
  detail: string | null
  payload: Record<string, any> | null
  created_at: string | null
}

export interface RunDetail {
  id: number
  run_id: string
  request_id: string
  sequence_no: number
  trigger_type: string | null
  transport: string | null
  request_mode: string | null
  input_message: string | null
  ask_user_answer_summary: string | null
  business_type: string | null
  run_status: string
  end_reason: string | null
  convergence_mode: string | null
  guard_error_code: string | null
  response_type: string | null
  response_preview: string | null
  response_payload: Record<string, any> | null
  response_metadata: Record<string, any> | null
  ask_user_question: string | null
  missing_fields: string[]
  ask_user_count: number
  tool_call_count: number
  external_tool_call_count: number
  tool_names: string[]
  has_error: boolean
  error_type: string | null
  error_message: string | null
  elapsed_ms: number | null
  started_at: string | null
  finished_at: string | null
  events: RunEventDetail[]
}

export interface LogDetail extends LogItem {
  final_response_payload: Record<string, any> | null
  error_message: string | null
  first_request_id: string | null
  last_request_id: string | null
  replaces_task_id: string | null
  replaced_by_task_id: string | null
  updated_at: string | null
  runs: RunDetail[]
}

export interface LogListParams {
  page: number
  page_size: number
  keyword?: string
  user_id?: string
  session_id?: string
  task_id?: string
  business_type?: string
  task_status?: string
  end_reason?: string
  convergence_mode?: string
  ask_user_triggered?: boolean
  has_error?: boolean
  uses_external_tools?: boolean
  tool_name?: string
  min_tool_calls?: number
  max_tool_calls?: number
  min_elapsed_ms?: number
  max_elapsed_ms?: number
  start_time?: string
  end_time?: string
}

export interface LogListResponse {
  total: number
  page: number
  page_size: number
  items: LogItem[]
}

export interface LogStats {
  total: number
  completed_count: number
  waiting_user_count: number
  guard_stopped_count: number
  failed_count: number
  switched_count: number
  ask_user_rate: number
  guard_stop_rate: number
  avg_elapsed_ms: number | null
  avg_tool_calls: number | null
  avg_external_tool_calls: number | null
  latest_created_at: string | null
  top_businesses: Array<{ business_type: string | null; count: number }>
}

export type ExportParams = Omit<LogListParams, 'page' | 'page_size'>

export const logsService = {
  async getList(params: LogListParams) {
    return api.get<LogListResponse>('/admin/logs/list', { params })
  },

  async getDetail(id: number) {
    return api.get<LogDetail>(`/admin/logs/${id}`)
  },

  async getStats(days: number = 7) {
    return api.get<LogStats>('/admin/logs/stats/summary', { params: { days } })
  },

  exportLogs(params: ExportParams) {
    const queryParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      queryParams.append(key, String(value))
    })

    const token = localStorage.getItem('token')
    const url = `/chat/api/admin/logs/export?${queryParams.toString()}`

    return fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
      .then(response => {
        if (!response.ok) {
          throw new Error('导出失败')
        }
        return response.blob()
      })
      .then(blob => {
        const blobUrl = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = `chat_task_logs_${new Date().getTime()}.csv`
        link.style.display = 'none'
        document.body.appendChild(link)
        link.click()
        setTimeout(() => {
          document.body.removeChild(link)
          window.URL.revokeObjectURL(blobUrl)
        }, 100)
      })
  },
}
