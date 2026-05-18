/**
 * 阿里云实时语音识别服务（网页端）
 *
 * 实现边说边识别的实时语音转文字功能
 * 网页端直连阿里云 NLS 服务，后端仅提供 Token
 */

// 生成阿里云 NLS 要求的 message_id/task_id
function generateNlsId(): string {
  const uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
  return uuid.replace(/-/g, '')
}

// 阿里云消息类型
interface AliyunMessage {
  header: {
    namespace: string
    name: string
    status: number
    status_text?: string
    message_id: string
    task_id: string
  }
  payload?: {
    index?: number
    time?: number
    begin_time?: number
    result?: string
    confidence?: number
    words?: any[]
  }
}

// Token 响应类型
interface TokenResponse {
  success: boolean
  token: string
  expire_time: number
  app_key: string
  ws_url: string
  error?: string
}

export interface AliyunSpeechCallbacks {
  onStart?: () => void
  onPartialResult?: (text: string, index: number) => void
  onFinalResult?: (text: string, index: number) => void
  onError?: (error: string) => void
  onStop?: () => void
}

// 语音配置
const SPEECH_CONFIG = {
  tokenEndpoint: '/chat/api/speech/aliyun/token',
  format: 'pcm',
  sampleRate: 16000,
  enableIntermediateResult: true,
  enablePunctuation: true,
  enableITN: true,
  maxSentenceSilence: 800,
}

class AliyunSpeechService {
  private websocket: WebSocket | null = null
  private audioContext: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private stream: MediaStream | null = null
  private callbacks: AliyunSpeechCallbacks = {}

  private isRecording = false
  private isConnected = false
  private currentText = ''

  // Token 相关
  private token = ''
  private tokenExpireTime = 0
  private appKey = ''
  private wsUrl = ''

  // 任务标识
  private taskId = ''

  // 是否已发送开始命令
  private startCommandSent = false

  // 录音开始时间（用于避免过短录音）
  private recordStartAt = 0
  private stopTimer: ReturnType<typeof setTimeout> | null = null

  constructor() {
    // 延迟初始化
  }

  /**
   * 检查 Token 是否有效
   */
  private isTokenValid(): boolean {
    if (!this.token || !this.tokenExpireTime) return false
    // 提前 5 分钟认为过期
    return this.tokenExpireTime > Math.floor(Date.now() / 1000) + 300
  }

  /**
   * 获取 Token
   */
  private async fetchToken(): Promise<void> {
    if (this.isTokenValid()) {
      console.log('[AliyunSpeech] Using cached token')
      return
    }

    console.log('[AliyunSpeech] Fetching new token...')

    try {
      const res = await fetch(SPEECH_CONFIG.tokenEndpoint)
      const data: TokenResponse = await res.json()

      if (!res.ok || !data.success) {
        throw new Error(data.error || 'Token 获取失败')
      }

      this.token = data.token
      this.tokenExpireTime = data.expire_time
      this.appKey = data.app_key
      this.wsUrl = data.ws_url

      console.log('[AliyunSpeech] Token fetched successfully, expire:', this.tokenExpireTime)
    } catch (err: any) {
      console.error('[AliyunSpeech] Token fetch failed:', err)
      throw new Error(err.message || 'Token 获取失败')
    }
  }

  /**
   * 检查浏览器是否支持
   */
  isSupported(): boolean {
    const secure =
      window.isSecureContext ||
      location.protocol === 'https:' ||
      location.hostname === 'localhost' ||
      location.hostname === '127.0.0.1'

    if (!secure) {
      console.warn('[AliyunSpeech] 需要 HTTPS 安全连接')
      return false
    }

    if (!navigator.mediaDevices || !window.AudioContext) {
      console.warn('[AliyunSpeech] 浏览器不支持音频录制')
      return false
    }

    return true
  }

  /**
   * 检查服务是否可用（测试 Token 接口）
   */
  async checkAvailability(): Promise<boolean> {
    try {
      const res = await fetch(SPEECH_CONFIG.tokenEndpoint)
      const data: TokenResponse = await res.json()
      return res.ok && data.success
    } catch {
      return false
    }
  }

  /**
   * 开始识别
   */
  async start(callbacks: AliyunSpeechCallbacks = {}): Promise<void> {
    this.callbacks = callbacks
    this.currentText = ''
    this.taskId = generateNlsId()
    this.startCommandSent = false

    // 防止重复启动
    if (this.isRecording || this.isConnected || this.websocket) {
      callbacks.onError?.('语音识别正在进行中')
      return
    }

    // 检查浏览器支持
    if (!this.isSupported()) {
      callbacks.onError?.('当前环境不支持语音识别')
      return
    }

    // 获取 Token 并连接
    await this.connectAndStart()
  }

  /**
   * 获取 Token 并建立连接
   */
  private async connectAndStart(): Promise<void> {
    try {
      // 1. 获取 Token
      await this.fetchToken()

      if (!this.token || !this.appKey) {
        throw new Error('Token 或 AppKey 无效')
      }

      // 2. 建立 WebSocket 连接
      const url = `${this.wsUrl}?token=${this.token}`
      console.log('[AliyunSpeech] Connecting to:', url.slice(0, 80) + '...')

      this.websocket = new WebSocket(url)

      // 连接成功
      this.websocket.onopen = () => {
        console.log('[AliyunSpeech] Socket connected')
        this.isConnected = true
        // 发送开始识别命令
        this.sendStartCommand()
      }

      // 接收消息
      this.websocket.onmessage = (event) => {
        this.handleMessage(event.data as string)
      }

      // 连接关闭
      this.websocket.onclose = (event) => {
        console.log('[AliyunSpeech] Socket closed, code:', event.code)
        this.isConnected = false
        if (this.isRecording) {
          this.stopAudioCapture()
        }
      }

      // 连接错误
      this.websocket.onerror = (err) => {
        console.error('[AliyunSpeech] Socket error:', err)
        this.isConnected = false
        this.callbacks.onError?.('语音服务连接错误')
        this.cleanup()
      }
    } catch (err: any) {
      console.error('[AliyunSpeech] Connect failed:', err)
      this.callbacks.onError?.(err.message || '连接失败')
      this.cleanup()
    }
  }

  /**
   * 发送开始识别命令
   */
  private sendStartCommand(): void {
    if (!this.websocket || !this.isConnected) return

    const command = {
      header: {
        message_id: generateNlsId(),
        task_id: this.taskId,
        namespace: 'SpeechTranscriber',
        name: 'StartTranscription',
        appkey: this.appKey,
      },
      payload: {
        format: SPEECH_CONFIG.format,
        sample_rate: SPEECH_CONFIG.sampleRate,
        enable_intermediate_result: SPEECH_CONFIG.enableIntermediateResult,
        enable_punctuation_prediction: SPEECH_CONFIG.enablePunctuation,
        enable_inverse_text_normalization: SPEECH_CONFIG.enableITN,
        max_sentence_silence: SPEECH_CONFIG.maxSentenceSilence,
      },
    }

    this.websocket.send(JSON.stringify(command))
    console.log('[AliyunSpeech] Start command sent')
  }

  /**
   * 发送停止识别命令
   */
  private sendStopCommand(): void {
    if (!this.websocket || !this.isConnected) return

    const command = {
      header: {
        message_id: generateNlsId(),
        task_id: this.taskId,
        namespace: 'SpeechTranscriber',
        name: 'StopTranscription',
        appkey: this.appKey,
      },
    }

    this.websocket.send(JSON.stringify(command))
    console.log('[AliyunSpeech] Stop command sent')
  }

  /**
   * 处理服务端消息
   */
  private handleMessage(data: string): void {
    try {
      const msg: AliyunMessage = JSON.parse(data)
      const name = msg.header?.name
      const status = msg.header?.status

      console.log('[AliyunSpeech] Received:', name, 'status:', status)

      // 检查错误状态
      if (status !== 20000000) {
        if (name === 'TaskFailed') {
          const rawError = msg.header?.status_text || '识别任务失败'
          const errorMsg = rawError.includes('NO_VALID_AUDIO_ERROR')
            ? '没录到有效声音，请按住麦克风说话再试'
            : rawError
          console.error('[AliyunSpeech] Task failed:', errorMsg)
          this.callbacks.onError?.(errorMsg)
          this.cleanup()
          return
        }
      }

      switch (name) {
        case 'TranscriptionStarted':
          // 服务端准备好了，开始录音
          console.log('[AliyunSpeech] Transcription started, starting recorder...')
          this.startCommandSent = true
          this.startAudioCapture()
          this.callbacks.onStart?.()
          break

        case 'TranscriptionResultChanged':
          // 中间结果
          if (msg.payload?.result) {
            this.currentText = msg.payload.result
            this.callbacks.onPartialResult?.(this.currentText, msg.payload.index || 0)
          }
          break

        case 'SentenceBegin':
          // 一句话开始
          console.log('[AliyunSpeech] Sentence begin, index:', msg.payload?.index)
          break

        case 'SentenceEnd':
          // 一句话结束（最终结果）
          if (msg.payload?.result) {
            const finalText = msg.payload.result
            console.log('[AliyunSpeech] Sentence end:', finalText)
            this.callbacks.onFinalResult?.(finalText, msg.payload.index || 0)
          }
          break

        case 'TranscriptionCompleted':
          // 识别完成
          console.log('[AliyunSpeech] Transcription completed')
          this.callbacks.onStop?.()
          this.cleanup()
          break

        default:
          console.log('[AliyunSpeech] Unknown message:', name)
      }
    } catch (e) {
      console.error('[AliyunSpeech] Parse message failed:', e)
    }
  }

  /**
   * 重采样函数：将音频从原始采样率转换到目标采样率
   */
  private resampleAudio(
    inputData: Float32Array,
    inputSampleRate: number,
    outputSampleRate: number
  ): Float32Array {
    if (inputSampleRate === outputSampleRate) {
      return inputData
    }
    const ratio = inputSampleRate / outputSampleRate
    const outputLength = Math.floor(inputData.length / ratio)
    const output = new Float32Array(outputLength)
    for (let i = 0; i < outputLength; i++) {
      const srcIndex = i * ratio
      const srcIndexFloor = Math.floor(srcIndex)
      const srcIndexCeil = Math.min(srcIndexFloor + 1, inputData.length - 1)
      const t = srcIndex - srcIndexFloor
      output[i] = inputData[srcIndexFloor] * (1 - t) + inputData[srcIndexCeil] * t
    }
    return output
  }

  /**
   * 开始音频采集
   */
  private async startAudioCapture(): Promise<void> {
    try {
      // 获取麦克风权限
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })

      // 创建音频上下文
      this.audioContext = new AudioContext()
      const actualSampleRate = this.audioContext.sampleRate
      console.log('[AliyunSpeech] Audio sample rate:', actualSampleRate)

      const source = this.audioContext.createMediaStreamSource(this.stream)

      // 使用 ScriptProcessorNode 获取 PCM 数据
      this.processor = this.audioContext.createScriptProcessor(4096, 1, 1)

      this.processor.onaudioprocess = (e) => {
        if (this.websocket?.readyState === WebSocket.OPEN && this.startCommandSent) {
          const inputData = e.inputBuffer.getChannelData(0)

          // 重采样到 16kHz
          const resampledData = this.resampleAudio(inputData, actualSampleRate, 16000)

          // 转换为 16bit PCM
          const pcmData = new Int16Array(resampledData.length)
          for (let i = 0; i < resampledData.length; i++) {
            const s = Math.max(-1, Math.min(1, resampledData[i]))
            pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7fff
          }

          // 发送二进制数据
          this.websocket.send(pcmData.buffer)
        }
      }

      source.connect(this.processor)
      this.processor.connect(this.audioContext.destination)

      this.isRecording = true
      this.recordStartAt = Date.now()
      console.log('[AliyunSpeech] Audio capture started')
    } catch (err: any) {
      console.error('[AliyunSpeech] Failed to start audio capture:', err)

      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        this.callbacks.onError?.('麦克风权限被拒绝，请在浏览器设置中允许访问')
      } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
        this.callbacks.onError?.('未检测到麦克风设备')
      } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
        this.callbacks.onError?.('麦克风被其他应用占用')
      } else {
        this.callbacks.onError?.(`无法启动录音: ${err.message || '未知错误'}`)
      }

      this.cleanup()
    }
  }

  /**
   * 停止音频采集
   */
  private stopAudioCapture(): void {
    if (this.processor) {
      this.processor.disconnect()
      this.processor = null
    }

    if (this.audioContext) {
      this.audioContext.close()
      this.audioContext = null
    }

    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop())
      this.stream = null
    }

    this.isRecording = false
    console.log('[AliyunSpeech] Audio capture stopped')
  }

  /**
   * 停止识别
   */
  stop(): void {
    console.log('[AliyunSpeech] Stopping...')

    if (this.isRecording) {
      // 避免录音过短
      const MIN_RECORD_MS = 600
      const elapsed = this.recordStartAt ? Date.now() - this.recordStartAt : MIN_RECORD_MS
      if (elapsed < MIN_RECORD_MS) {
        const waitMs = MIN_RECORD_MS - elapsed
        if (this.stopTimer) clearTimeout(this.stopTimer)
        this.stopTimer = setTimeout(() => {
          this.stopTimer = null
          this.stop()
        }, waitMs)
        return
      }

      this.stopAudioCapture()
    }

    this.sendStopCommand()
  }

  /**
   * 清理资源
   */
  private cleanup(): void {
    this.stopAudioCapture()
    this.isConnected = false
    this.startCommandSent = false
    this.recordStartAt = 0

    if (this.stopTimer) {
      clearTimeout(this.stopTimer)
      this.stopTimer = null
    }

    if (this.websocket) {
      try {
        this.websocket.close()
      } catch (e) {
        // 忽略关闭错误
      }
      this.websocket = null
    }
  }

  /**
   * 获取当前状态
   */
  getStatus(): { isRecording: boolean; isConnected: boolean } {
    return {
      isRecording: this.isRecording,
      isConnected: this.isConnected,
    }
  }

  /**
   * 获取当前识别文本
   */
  getCurrentText(): string {
    return this.currentText
  }
}

// 导出单例
export const aliyunSpeechService = new AliyunSpeechService()
