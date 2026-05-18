/**
 * 旧版聊天 API 封装
 * 通过后端代理调用微信小程序后端
 */

import type {
  QuestionRequest,
  QuestionResponse,
  AnswerRequest,
  AnswerResponse,
  ConversationsRequest,
  ConversationsResponse,
  FeedbackRequest,
  FeedbackResponse,
  StsTokenResponse
} from '../types/legacy'

const API_BASE = '/chat/api/legacy'

/**
 * 通用请求函数
 */
async function request<T>(
  endpoint: string,
  data?: Record<string, any>
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: data ? JSON.stringify(data) : undefined,
  })

  if (!response.ok) {
    throw new Error(`API Error: ${response.status}`)
  }

  return response.json()
}

/**
 * 发送提问
 */
export async function sendQuestion(
  conversationId: string,
  question: string,
  imageList?: string[]
): Promise<QuestionResponse> {
  const data: QuestionRequest = {
    conversation_id: conversationId,
    question,
  }
  if (imageList && imageList.length > 0) {
    data.image_list = imageList
  }
  return request<QuestionResponse>('/v1/question', data)
}

/**
 * 轮询答案
 */
export async function pollAnswer(
  conversationId: string,
  questionId: string
): Promise<AnswerResponse> {
  const data: AnswerRequest = {
    conversation_id: conversationId,
    question_id: questionId,
  }
  return request<AnswerResponse>('/v1/answer', data)
}

/**
 * 获取历史消息
 */
export async function getConversations(
  conversationId: string,
  page: number = 1,
  pageSize: number = 20
): Promise<ConversationsResponse> {
  const data: ConversationsRequest = {
    conversation_id: conversationId,
    page,
    page_size: pageSize,
  }
  return request<ConversationsResponse>('/v1/conversations', data)
}

/**
 * 提交反馈
 */
export async function submitFeedback(
  questionId: string,
  feedbackType: 'like' | 'dislike' | 'copy' | 'retry'
): Promise<FeedbackResponse> {
  const data: FeedbackRequest = {
    question_id: questionId,
    feedback_type: feedbackType,
  }
  return request<FeedbackResponse>('/v1/feedback', data)
}

/**
 * 获取 OSS STS Token（用于图片上传）
 */
export async function getStsToken(): Promise<StsTokenResponse> {
  return request<StsTokenResponse>('/sts/get_token_public')
}

/**
 * 检查旧版 API 可用性
 */
export async function checkLegacyHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/health`)
    const data = await response.json()
    return data.status === 'ok'
  } catch {
    return false
  }
}

/**
 * 生成会话 ID（与微信小程序逻辑保持一致）
 * 格式：web_<timestamp>_<random>
 */
export function generateConversationId(): string {
  const timestamp = Date.now()
  const random = Math.random().toString(36).substring(2, 10)
  return `web_${timestamp}_${random}`
}
