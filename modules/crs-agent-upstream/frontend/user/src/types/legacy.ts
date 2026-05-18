/**
 * 旧版聊天系统类型定义
 * 对接微信小程序后端 API
 */

// ==================== 消息类型 ====================

export interface LegacyMessage {
  id: string
  conversation_id: string
  question_id?: string
  role: 'user' | 'assistant'
  content: string
  image_list?: string[]
  timestamp: number
  status: 'pending' | 'generating' | 'completed' | 'failed'
  feedback?: 'like' | 'dislike' | null
}

// ==================== API 请求类型 ====================

export interface QuestionRequest {
  conversation_id: string
  question: string
  image_list?: string[]
}

export interface AnswerRequest {
  conversation_id: string
  question_id: string
}

export interface ConversationsRequest {
  conversation_id: string
  page?: number
  page_size?: number
}

export interface FeedbackRequest {
  question_id: string
  feedback_type: 'like' | 'dislike' | 'copy' | 'retry'
}

// ==================== API 响应类型 ====================

export interface QuestionResponse {
  code: number
  message: string
  data?: {
    question_id: string
  }
}

export interface AnswerResponse {
  code: number
  message: string
  data?: {
    status: 'generating' | 'completed' | 'failed'
    answer?: string
    error?: string
  }
}

export interface ConversationsResponse {
  code: number
  message: string
  data?: {
    total: number
    list: Array<{
      question_id: string
      question: string
      answer: string
      image_list?: string[]
      timestamp: number
      feedback?: 'like' | 'dislike' | null
    }>
  }
}

export interface FeedbackResponse {
  code: number
  message: string
}

export interface StsTokenResponse {
  code: number
  message: string
  data?: {
    credentials: {
      accessKeyId: string
      accessKeySecret: string
      securityToken: string
    }
    expiration?: string
  }
}

// ==================== 聊天状态 ====================

export interface LegacyChatState {
  conversationId: string
  messages: LegacyMessage[]
  isLoading: boolean
  isPolling: boolean
  currentQuestionId: string | null
  error: string | null
}
