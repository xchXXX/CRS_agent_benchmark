/**
 * API 响应类型定义（用户端内置版）
 *
 * 目的：让 frontend/user 可独立迁移/打包，不依赖跨目录 shared 包。
 */

// ==================== 搜索相关 ====================

export interface SearchResult {
  doc_id: string
  file_id: string
  title: string
  path: string
  tags: {
    brand?: string
    series?: string
    model?: string
    model_variants?: string[]
    platform_codes?: string[]
    subsystems?: string[]
    ecus?: string[]
    suppliers?: string[]
    emissions?: string[]
    drive_types?: string[]
    batches?: string[]
    doc_types?: string[]
  }
  score: number
  explain: string[]
}

export interface ClarifyOption {
  need: boolean
  facet?: string
  question?: string
  options?: string[]
}

export interface SearchResponse {
  results: SearchResult[]
  clarify: ClarifyOption
  stats: {
    took_ms: number
    candidates: number
    debug_info?: any
  }
}

export interface FilePreview {
  file_type: 'pdf' | 'image' | 'text'
  content: string
  current_page?: number
  total_pages?: number
}

export interface SystemStats {
  total_files: number
  total_docs: number
  index_size_mb: number
  last_update: string
  pending_changes: number
}

// ==================== 聊天相关 ====================

export type Mode = 'auto' | 'fault_diagnosis' | 'doc_search' | 'general_chat' | 'param_query'

export interface ModeOption {
  key: Mode
  label: string
  shortLabel: string
  icon: string
  description: string
}

export const MODE_OPTIONS: ModeOption[] = [
  {
    key: 'auto',
    label: '自动模式',
    shortLabel: '自动',
    icon: 'sparkles',
    description: '智能识别意图，自动选择最佳处理方式'
  },
  {
    key: 'fault_diagnosis',
    label: '故障诊断',
    shortLabel: '诊断',
    icon: 'alert-triangle',
    description: '输入故障码获取诊断报告'
  },
  {
    key: 'doc_search',
    label: '资料搜索',
    shortLabel: '搜索',
    icon: 'search',
    description: '搜索电路图、维修手册等资料'
  },
  {
    key: 'general_chat',
    label: '维修知识',
    shortLabel: '问答',
    icon: 'message-circle',
    description: '咨询维修技术、原理相关问题'
  },
  {
    key: 'param_query',
    label: '参数查询',
    shortLabel: '参数',
    icon: 'cpu',
    description: '查询 ECU 针脚、电压和定义等结构化参数'
  }
]

export type BusinessType =
  | 'IDLE'
  | 'INTENT_CLARIFYING'
  | 'DOC_SEARCH'
  | 'PARAM_QUERY'
  | 'FAULT_DIAGNOSIS'
  | 'GENERAL_CHAT'
  | 'AGENT_LOOP'

/** 生命周期状态 */
export type Lifecycle = 'idle' | 'ongoing' | 'completed'

export type ResponseType =
  | 'message'
  | 'ask_user'
  | 'documents'
  | 'fault'
  | 'text'
  | 'text_stream'
  | 'clarify_intent'
  | 'clarify_business'
  | 'param_request'
  | 'error'

export interface ChatClarifyOption {
  key: string
  label: string
  description?: string
  selection_payload?: Record<string, any>
}

export interface AskUserOption {
  key: string
  label: string
  description?: string
  selection_payload?: Record<string, any>
}

export interface AskUserQuestion {
  tool_call_id: string
  question: string
  input_type: 'single_select' | 'multi_select' | 'number' | 'text'
  options: AskUserOption[]
  allow_free_input: boolean
  input_hint?: string
  unit?: string
  reference_range?: string
  context?: Record<string, any>
}

export interface AskUserAnswer {
  tool_call_id: string
  answer: any
  metadata?: Record<string, any>
}

export interface SuggestedQuestion {
  text: string
  query: string
  action_type: 'auto' | 'fault_diagnosis' | 'doc_search' | 'general_chat' | 'param_query' | 'none'
  priority: number
}

export interface RepairKnowledgeSourceRef {
  id: string
  title: string
  relation: 'primary' | 'related'
  match_score: number
}

export interface RepairKnowledgeSourceDetail {
  id: string
  title: string
  topic?: string
  content: string
}

export interface ParameterQuerySourceRef {
  id: string
  title: string
  relation: 'primary' | 'related'
  match_score: number
}

export interface ParameterQuerySourceDetail {
  id: string
  title: string
  ecu_name?: string
  system_voltage?: number
  content: string
}

export interface ParameterQueryRow {
  id: string
  row_no: number
  component_name?: string
  ecu_pin_no?: string
  pin_definition?: string
  connector_pin_no?: string
  open_voltage_text?: string
  static_voltage_text?: string
  idle_voltage_text?: string
  remark?: string
  requested_value?: string
}

export interface ParameterQueryContent {
  query: string
  summary: string
  requested_field?: string
  requested_field_label?: string
  selected_source: {
    id: string
    title: string
    ecu_name?: string
    system_voltage?: number
    pin_doc_kind?: string
  }
  rows: ParameterQueryRow[]
  source_refs: ParameterQuerySourceRef[]
}

export type ClientType = 'web' | 'miniapp'

export interface ChatRequest {
  message: string
  session_id?: string
  context?: {
    clarify_choice?: string
    clarify_facet?: string
    image_evidence?: ImageEvidenceAnalysis
    image_evidences?: ImageEvidenceAnalysis[]
    [key: string]: any
  }
  ask_user_answer?: AskUserAnswer
  mode?: Mode
  client_type?: ClientType
  /** 生命周期检查信息 */
  lifecycle_check?: {
    current_lifecycle?: Lifecycle
    current_business?: BusinessType
    has_ongoing?: boolean
    user_confirmed_switch?: boolean
  }
}

/** 结果摘要 */
export interface ResultSummary {
  question: string
  result_type: 'search' | 'diagnosis' | 'batch' | 'chat'
  result_count?: number
  preview: string
  display_title: string
  display_subtitle: string
  can_collapse: boolean
}

/** 冲突信息 */
export interface ConflictInfo {
  detected: boolean
  reason: string
  message: string
  current_business?: BusinessType
  recommended_action: string
  context_info?: {
    clarify_round?: number
    business_duration?: number
  }
}

/** 生命周期信息 */
export interface LifecycleInfo {
  previous_lifecycle?: Lifecycle
  current_lifecycle: Lifecycle
  state_changed: boolean
  conflict?: ConflictInfo
}

export interface ChatResponse {
  type: ResponseType
  content: any
  session_id: string
  business: BusinessType | null
  /** 生命周期信息 */
  lifecycle_info?: LifecycleInfo
  /** 结果摘要 */
  result_summary?: ResultSummary
  need_clarify: boolean
  clarify_options: ChatClarifyOption[]
  clarify_facet?: string
  suggestions?: SuggestedQuestion[]
  /** 系统提示，如新搜索提醒 */
  hints?: Array<{ type: string; message: string }>
  metadata?: {
    timestamp?: string
    conflict_type?: string
    [key: string]: any
  }
  /** 请求唯一标识，用于反馈关联 */
  request_id?: string
  ask_user?: AskUserQuestion
}

export interface DocumentsContent {
  query: string
  total: number
  documents: Array<{
    id: string
    title: string
    path: string
    tags: Record<string, any>
    score: number
  }>
}

export interface FaultContent {
  state: 'ready' | 'generating' | 'failed'
  faultCode: string
  ecuModel: string
  reportUrl?: string
  taskId?: string
  subscribeUrl?: string
  message: string
  error?: {
    code: string
    message: string
  }
}

export interface IntentClarifyContent {
  message: string
  entity?: string
}

export interface BusinessClarifyContent {
  message: string
  query?: string
  results_count?: number
  clarify_round?: number
}

export interface TaskStatus {
  taskId: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  progress?: number
  result?: any
  error?: string
}

export interface Notification {
  id: string
  type: 'success' | 'info' | 'warning' | 'error'
  title: string
  message: string
  action?: {
    label: string
    url?: string
    onClick?: () => void
  }
  timestamp: Date
  dismissed?: boolean
}

// ==================== 图片诊断相关 ====================

export interface RecognizedFaultCode {
  raw: string
  normalized: string
  type: string
  description: string
  status: string | null
  selected?: boolean
}

export interface ImageRecognitionResponse {
  success: boolean
  fault_codes: RecognizedFaultCode[]
  count: number
  image_evidence?: ImageEvidenceAnalysis
  error?: string
}

export type ImageEvidenceScene =
  | 'vehicle_identity'
  | 'diagnostic_screen'
  | 'repair_scene'
  | 'document_hint'
  | 'unknown'

export interface ImageEvidenceVehicleInfo {
  brand?: string | null
  series?: string | null
  model?: string | null
  platform?: string | null
  engine?: string | null
  emission?: string | null
  vin?: string | null
  license_plate?: string | null
}

export interface ImageEvidenceDiagnosticInfo {
  fault_codes: string[]
  descriptions: string[]
  ecu_model?: string | null
  status?: string | null
}

export interface ImageEvidenceAnalysis {
  image_evidence_id: string
  scene: ImageEvidenceScene
  summary: string
  vehicle: ImageEvidenceVehicleInfo
  diagnosis: ImageEvidenceDiagnosticInfo
  visible_text: string[]
  suggested_queries: string[]
  confidence: number
  needs_user_confirm: boolean
  raw?: Record<string, any>
}

export interface ImageEvidenceResponse {
  success: boolean
  evidence?: ImageEvidenceAnalysis | null
  error?: {
    code?: string
    message?: string
    [key: string]: any
  } | null
}

export interface EcuSummaryItem {
  ecu_model: string
  match_count: number
  matched_codes: string[]
  recommended: boolean
}

export interface BatchEcusResponse {
  success: boolean
  ecu_summary: EcuSummaryItem[]
  code_details: Record<string, string[]>
  error?: string
}

export interface BatchReportItem {
  fault_code: string
  state: 'ready' | 'generating' | 'not_found'
  report_url: string | null
  task_id: string | null
  subscribe_url: string | null
  report_id: number | null
}

export interface BatchReportsResponse {
  success: boolean
  ecu_model: string
  reports: BatchReportItem[]
  error?: string
}

export type ImageDiagStep =
  | 'idle'
  | 'uploading'
  | 'recognizing'
  | 'selecting_codes'
  | 'selecting_ecu'
  | 'diagnosing'
  | 'done'

export interface ImageDiagState {
  step: ImageDiagStep
  imageFile?: any
  imagePreview?: string
  recognizedCodes: RecognizedFaultCode[]
  selectedCodes: string[]
  ecuOptions: EcuSummaryItem[]
  selectedEcu?: string
  diagnosisResults: BatchReportItem[]
  error?: string
}
