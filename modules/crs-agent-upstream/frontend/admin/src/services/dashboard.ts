import api from './api'

export interface DashboardSummary {
  dimensions: {
    facet_count: number
    value_count: number
    cache_loaded: boolean
  }
  logs: {
    total_count: number
    last_7d_count: number
    avg_elapsed_ms_7d: number | null
    latest_created_at: string | null
    top_businesses: Array<{ business_type: string | null; count: number }>
    status_distribution: Array<{ task_status: string | null; count: number }>
  }
  feedback: {
    total_count: number
    last_30d_count: number
    avg_rating_30d: number | null
    with_comment_30d: number
    latest_created_at: string | null
  }
  benchmarks: {
    dataset_count: number
    total_cases: number
    run_count: number
    running_count: number
    latest_run_at: string | null
    latest_recall_at_10: number | null
    latest_track: string | null
  }
}

export const dashboardService = {
  getSummary: () => api.get<DashboardSummary>('/admin/dashboard/summary')
}
