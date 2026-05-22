/**
 * API 客户端封装
 */

import axios from 'axios'
import type {
  SearchResponse,
  FilePreview,
  SystemStats,
  ChatRequest,
  ChatResponse,
  ImageEvidenceResponse,
  ImageRecognitionResponse,
  BatchEcusResponse,
  BatchReportsResponse,
  RepairKnowledgeSourceDetail,
  ParameterQuerySourceDetail
} from '@/types'
import { getStoredToken, clearStoredToken } from '@/utils/tokenValidator'
import jsBridge from '@/utils/jsBridge'

const api = axios.create({
  baseURL: '/chat/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    const token = getStoredToken()
    if (token) {
      config.headers['x-app-token'] = token
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error) => {
    if (error.response?.status === 401) {
      const detail = error.response?.data?.detail || error.response?.data?.message || '登录已失效，请重新登录'
      clearStoredToken()
      alert(detail)
      setTimeout(() => jsBridge.closeWebView(), 100)
      return Promise.reject(error)
    }
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

/**
 * 搜索接口（旧版，保留作为降级方案）
 */
export const search = async (
  query: string,
  filters?: Record<string, any>,
  clarifychoice?: string
): Promise<SearchResponse> => {
  return api.post(
    '/search',
    {
      query,
      filters,
      clarify_choice: clarifychoice,
      limit: 20,
    },
    {
      // 资料搜索可能触发后端多轮澄清分析，在线模型场景下需要更长超时。
      timeout: 120000,
    }
  )
}

/**
 * 聊天接口（新版统一入口）
 */
export const chat = async (request: ChatRequest): Promise<ChatResponse> => {
  return api.post('/chat/completions', request, {
    // OpenRouter + doc_search 多轮工具调用在真实环境下可能超过默认 30 秒。
    timeout: 180000,
  })
}

const createChatImageFormData = (
  request: ChatRequest,
  imageFiles: Array<File | Blob>
): FormData => {
  const formData = new FormData()
  formData.append('request', JSON.stringify(request))
  imageFiles.forEach((imageFile, index) => {
    formData.append(
      'images',
      imageFile,
      imageFile instanceof File ? imageFile.name : `image_${index + 1}.jpg`
    )
  })
  return formData
}

const createAuthHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = {}
  const appToken = getStoredToken()
  if (appToken) {
    headers['x-app-token'] = appToken
  }
  return headers
}

const handleAuthFailure = async (response: Response): Promise<void> => {
  if (response.status !== 401) return
  let message = '登录已失效，请重新登录'
  try {
    const payload = await response.clone().json()
    message = payload?.detail || payload?.message || message
  } catch {
    // ignore
  }
  clearStoredToken()
  alert(message)
  setTimeout(() => jsBridge.closeWebView(), 100)
}

export const chatWithImages = async (
  request: ChatRequest,
  imageFiles: Array<File | Blob>
): Promise<ChatResponse> => {
  const response = await fetch('/chat/api/chat/completions-with-images', {
    method: 'POST',
    headers: createAuthHeaders(),
    body: createChatImageFormData(request, imageFiles),
  })

  if (!response.ok) {
    await handleAuthFailure(response)
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return response.json()
}

/**
 * 获取会话信息
 */
export const getSession = async (sessionId: string): Promise<any> => {
  return api.get(`/chat/session/${sessionId}`)
}

/**
 * 删除会话
 */
export const deleteSession = async (sessionId: string): Promise<any> => {
  return api.delete(`/chat/session/${sessionId}`)
}

/**
 * 获取对话历史
 */
export const getChatHistory = async (sessionId: string, limit?: number): Promise<any> => {
  return api.get(`/chat/history/${sessionId}`, {
    params: { limit },
  })
}

/**
 * 获取文件预览
 */
export const getFilePreview = async (
  fileId: string,
  page?: number
): Promise<FilePreview> => {
  return api.get(`/file/${fileId}/preview`, {
    params: { page },
  })
}

export interface CircuitViewerPageInfo {
  page_index: number
  page_number: number
  width_px: number
  height_px: number
}

export interface CircuitViewerMetadata {
  pdf_id: string
  filename: string
  keyword: string
  initial_hit_id?: string
  initial_page_index: number
  initial_page_number: number
  initial_highlight_boxes_px?: number[][]
  total_pages: number
  pages: CircuitViewerPageInfo[]
  has_result_json?: boolean
  has_source_pdf_url?: boolean
}

export interface CircuitViewerHit {
  hit_id: string
  page_index: number
  page_number: number
  bbox_px: [number, number, number, number]
  points?: string
  matched_text: string
  context?: string
  reading_order?: number
  element_index?: number
  char_start?: number
}

export interface CircuitViewerSearchResponse {
  keyword: string
  pdf_id?: string
  initial_hit_id?: string
  total_matches: number
  positioned_match_count: number
  truncated: boolean
  results: CircuitViewerHit[]
  page_summary: Array<{ page_index: number; page_number: number; match_count: number }>
}

export const getCircuitViewerMetadata = async (
  token: string,
  signal?: AbortSignal
): Promise<CircuitViewerMetadata> => {
  return api.get(`/circuit-body-search/viewer/${encodeURIComponent(token)}/metadata`, { signal })
}

export const searchCircuitViewer = async (
  token: string,
  keyword: string,
  signal?: AbortSignal
): Promise<CircuitViewerSearchResponse> => {
  return api.post(
    `/circuit-body-search/viewer/${encodeURIComponent(token)}/search`,
    { keyword, limit: 200 },
    { signal }
  )
}

export const getCircuitViewerPageImageUrl = (token: string, pageIndex: number): string => (
  `/chat/api/circuit-body-search/viewer/${encodeURIComponent(token)}/page/${pageIndex}/image`
)

/**
 * 获取系统统计
 */
export const getSystemStats = async (): Promise<SystemStats> => {
  return api.get('/stats')
}

/**
 * 健康检查
 */
export const healthCheck = async (): Promise<{ status: string }> => {
  return api.get('/health')
}

/**
 * 聊天服务健康检查
 */
export const chatHealthCheck = async (): Promise<any> => {
  return api.get('/chat/health')
}

/**
 * 流式聊天接口
 * 使用 Server-Sent Events 进行流式输出
 */
export interface StreamCallbacks {
  onStart?: (sessionId: string) => void
  onHint?: (message: string) => void
  onChunk?: (chunk: string) => void
  onDone?: (fullContent: string, response?: ChatResponse) => void
  onFallback?: (response: ChatResponse) => void
  onError?: (error: string) => void
}

export const chatStream = async (
  request: ChatRequest,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> => {
  try {
    const appToken = getStoredToken()
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (appToken) {
      headers['x-app-token'] = appToken
    }

    const response = await fetch('/chat/api/chat/stream', {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
      signal,
    })

    if (!response.ok) {
      if (response.status === 401) {
        let message = '登录已失效，请重新登录'
        try {
          const payload = await response.clone().json()
          message = payload?.detail || payload?.message || message
        } catch {
          // ignore
        }
        clearStoredToken()
        alert(message)
        setTimeout(() => jsBridge.closeWebView(), 100)
      }
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('No response body')
    }

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        try {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const dataStr = line.slice(6)
              if (!dataStr) continue

              try {
                const data = JSON.parse(dataStr)

                switch (data.type) {
                  case 'start':
                    callbacks.onStart?.(data.session_id)
                    break
                  case 'hint':
                    callbacks.onHint?.(data.message)
                    break
                  case 'chunk':
                    callbacks.onChunk?.(data.content)
                    break
                  case 'done':
                    // request_id 可能在 data.response.request_id 或 data.request_id
                    if (data.response && data.request_id && !data.response.request_id) {
                      data.response.request_id = data.request_id
                    }
                    // 无 response 时构造一个最小的 response 携带 request_id
                    if (!data.response && data.request_id) {
                      data.response = { request_id: data.request_id } as ChatResponse
                    }
                    callbacks.onDone?.(data.full_content, data.response)
                    break
                  case 'fallback':
                    callbacks.onFallback?.(data.response)
                    break
                  case 'error':
                    callbacks.onError?.(data.message)
                    break
                }
              } catch {
                // 忽略解析错误
              }
            }
          }
        } catch (e) {
          // AbortError 是用户主动中断，不作为异常抛出
          if (e instanceof DOMException && e.name === 'AbortError') {
            return
          }
          throw e
        }
      }
    } finally {
      reader.releaseLock()
    }
  } catch (e) {
    // fetch 建连阶段也可能被 AbortController 中断
    if (e instanceof DOMException && e.name === 'AbortError') {
      return
    }
    throw e
  }
}

export const chatStreamWithImages = async (
  request: ChatRequest,
  imageFiles: Array<File | Blob>,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> => {
  try {
    const response = await fetch('/chat/api/chat/stream-with-images', {
      method: 'POST',
      headers: createAuthHeaders(),
      body: createChatImageFormData(request, imageFiles),
      signal,
    })

    if (!response.ok) {
      await handleAuthFailure(response)
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('No response body')
    }

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        try {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const dataStr = line.slice(6)
              if (!dataStr) continue

              try {
                const data = JSON.parse(dataStr)

                switch (data.type) {
                  case 'start':
                    callbacks.onStart?.(data.session_id)
                    break
                  case 'hint':
                    callbacks.onHint?.(data.message)
                    break
                  case 'chunk':
                    callbacks.onChunk?.(data.content)
                    break
                  case 'done':
                    if (data.response && data.request_id && !data.response.request_id) {
                      data.response.request_id = data.request_id
                    }
                    if (!data.response && data.request_id) {
                      data.response = { request_id: data.request_id } as ChatResponse
                    }
                    callbacks.onDone?.(data.full_content, data.response)
                    break
                  case 'fallback':
                    callbacks.onFallback?.(data.response)
                    break
                  case 'error':
                    callbacks.onError?.(data.message)
                    break
                }
              } catch {
                // 忽略解析错误
              }
            }
          }
        } catch (e) {
          if (e instanceof DOMException && e.name === 'AbortError') {
            return
          }
          throw e
        }
      }
    } finally {
      reader.releaseLock()
    }
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      return
    }
    throw e
  }
}

/**
 * 通知后端流式对话已被中断
 * 将已接收的部分内容保存到对话历史
 */
export const notifyStreamAbort = async (
  sessionId: string,
  partialContent: string
): Promise<void> => {
  try {
    await api.post('/chat/stream/abort', {
      session_id: sessionId,
      partial_content: partialContent,
    })
  } catch (e) {
    console.warn('通知流式中断失败:', e)
  }
}

// ==================== 图片诊断相关接口 ====================

/**
 * 查询图片诊断功能是否可用（依赖外部诊断服务开关）
 */
export const getDiagnosisAvailable = async (): Promise<{ available: boolean }> => {
  return api.get('/image/diagnosis-available')
}

/**
 * 查询通用图片证据识别是否可用
 */
export const getImageEvidenceAvailable = async (): Promise<{
  available: boolean
  max_images: number
  max_image_mb: number
}> => {
  return api.get('/image/evidence-available')
}

/**
 * 通用图片证据识别：车辆信息、诊断仪文字、故障码、资料线索
 */
export const analyzeImageEvidence = async (
  imageFiles: Array<File | Blob>
): Promise<ImageEvidenceResponse> => {
  const formData = new FormData()
  imageFiles.forEach((imageFile, index) => {
    formData.append(
      'images',
      imageFile,
      imageFile instanceof File ? imageFile.name : `image_${index + 1}.jpg`
    )
  })

  const fetchHeaders: Record<string, string> = {}
  const imgToken = getStoredToken()
  if (imgToken) {
    fetchHeaders['x-app-token'] = imgToken
  }

  const response = await fetch('/chat/api/image/analyze-evidence', {
    method: 'POST',
    headers: fetchHeaders,
    body: formData,
  })

  if (!response.ok) {
    throw new Error(`图片识别失败: ${response.status}`)
  }

  return response.json()
}

/**
 * 识别图片中的故障码
 */
export const recognizeFaultCodes = async (
  imageFile: File | Blob
): Promise<ImageRecognitionResponse> => {
  const formData = new FormData()
  formData.append('image', imageFile, imageFile instanceof File ? imageFile.name : 'image.jpg')

  const fetchHeaders: Record<string, string> = {}
  const imgToken = getStoredToken()
  if (imgToken) {
    fetchHeaders['x-app-token'] = imgToken
  }

  const response = await fetch('/chat/api/image/recognize-fault-codes', {
    method: 'POST',
    headers: fetchHeaders,
    body: formData,
  })

  if (!response.ok) {
    throw new Error(`识别失败: ${response.status}`)
  }

  return response.json()
}

/**
 * 批量查询故障码关联的ECU
 */
export const getBatchEcus = async (
  faultCodes: string[]
): Promise<BatchEcusResponse> => {
  return api.post('/diagnosis/batch-ecus', { fault_codes: faultCodes })
}

/**
 * 批量查询/生成诊断报告
 */
export const getBatchReports = async (
  faultCodes: string[],
  ecuModel: string
): Promise<BatchReportsResponse> => {
  return api.post('/diagnosis/batch-reports', {
    fault_codes: faultCodes,
    ecu_model: ecuModel,
  })
}

// ==================== 用户反馈接口 ====================

export interface FeedbackRequest {
  request_id: string
  session_id: string
  rating: number
  business_type: string
  tags?: string[]
  comment?: string
}

export const submitFeedback = async (data: FeedbackRequest): Promise<{ success: boolean; id: number }> => {
  return api.post('/feedback', data)
}

// ==================== 共轨之家文件链接接口 ====================

export interface FileUrlRequest {
  sn: number
  data_type: number
  file_no: string | null
  file_type: string | null
}

export interface FileUrlResponse {
  url: string | null
  url_type: string
  message?: string
}

export const getGgzjFileUrl = async (data: FileUrlRequest): Promise<FileUrlResponse> => {
  return api.post('/ggzj/file-url', data)
}

export const getRepairKnowledgeSource = async (
  entryId: string
): Promise<{ success: boolean; data?: RepairKnowledgeSourceDetail; message?: string }> => {
  return api.get(`/repair-knowledge/source/${entryId}`)
}

export const getParameterQuerySource = async (
  sourceId: string
): Promise<{ success: boolean; data?: ParameterQuerySourceDetail; message?: string }> => {
  return api.get(`/parameter-query/source/${sourceId}`)
}

export default api
