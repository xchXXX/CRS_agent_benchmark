/**
 * SSE 订阅服务
 *
 * 用于订阅后台异步任务的状态更新，如故障诊断报告生成。
 *
 * 接口规范：
 * - 事件类型：event: state
 * - 数据格式：{ taskId, ecuModel, dtcCode, state, reportId, reportViewerPath, error }
 * - state 枚举：queued, running, ready, failed
 */

export interface SSECallbacks {
  onProgress?: (progress: number, message?: string) => void
  onComplete: (result: { reportUrl?: string; reportId?: number }) => void
  onError: (error: Error) => void
}

/**
 * 诊断任务 SSE 响应数据
 */
interface DiagnosisSSEData {
  taskId: string
  ecuModel: string
  dtcCode: string
  state: 'queued' | 'running' | 'ready' | 'failed'
  reportId: number | null
  reportViewerPath: string
  error: { code: string; message: string } | null
  createdAt: string
  updatedAt: string
}

/**
 * 订阅任务状态
 *
 * @param taskId 任务ID
 * @param subscribeUrl SSE订阅地址
 * @param callbacks 回调函数集合
 * @returns 取消订阅函数
 */
export function subscribeToTask(
  taskId: string,
  subscribeUrl: string,
  callbacks: SSECallbacks
): () => void {
  const { onProgress, onComplete, onError } = callbacks

  let eventSource: EventSource | null = null
  let isClosing = false

  // 关闭连接函数
  function closeEventSource() {
    if (eventSource && !isClosing) {
      isClosing = true
      eventSource.close()
      eventSource = null
      console.log(`SSE 连接已关闭: taskId=${taskId}`)
    }
  }

  try {
    eventSource = new EventSource(subscribeUrl)

    // 监听命名事件 "state"（接口规范要求）
    eventSource.addEventListener('state', (event: MessageEvent) => {
      try {
        const data: DiagnosisSSEData = JSON.parse(event.data)
        console.log('SSE state 事件:', data)

        switch (data.state) {
          case 'queued':
          case 'running':
            // 进度更新
            if (onProgress) {
              const progress = data.state === 'queued' ? 10 : 50
              onProgress(progress, data.state === 'queued' ? '排队中...' : '生成中...')
            }
            break

          case 'ready':
            // 任务完成
            onComplete({
              reportUrl: data.reportViewerPath
                ? `${window.location.protocol}//${window.location.host}${data.reportViewerPath}`
                : undefined,
              reportId: data.reportId ?? undefined
            })
            closeEventSource()
            break

          case 'failed':
            // 任务失败
            const errorMsg = data.error?.message || '报告生成失败'
            onError(new Error(errorMsg))
            closeEventSource()
            break
        }
      } catch (parseError) {
        console.error('SSE 消息解析失败:', parseError, event.data)
      }
    })

    // 处理错误（包括连接错误）
    eventSource.onerror = (error) => {
      if (!isClosing) {
        console.error('SSE 连接错误:', error)
        // 检查是否是连接被服务端正常关闭
        if (eventSource?.readyState === EventSource.CLOSED) {
          // 可能是服务端正常关闭，不报错
          console.log('SSE 连接被服务端关闭')
        } else {
          onError(new Error('SSE 连接中断'))
        }
        closeEventSource()
      }
    }

    // 处理打开事件
    eventSource.onopen = () => {
      console.log(`SSE 连接已建立: taskId=${taskId}`)
    }

  } catch (error) {
    onError(error instanceof Error ? error : new Error('创建 SSE 连接失败'))
  }

  // 返回取消订阅函数
  return closeEventSource
}

/**
 * 任务管理器
 *
 * 用于管理多个 SSE 订阅
 */
export class TaskManager {
  private subscriptions: Map<string, () => void> = new Map()

  /**
   * 订阅任务
   */
  subscribe(taskId: string, subscribeUrl: string, callbacks: SSECallbacks): void {
    // 如果已存在订阅，先取消
    this.unsubscribe(taskId)

    const unsubscribe = subscribeToTask(taskId, subscribeUrl, {
      ...callbacks,
      onComplete: (result) => {
        this.subscriptions.delete(taskId)
        callbacks.onComplete(result)
      },
      onError: (error) => {
        this.subscriptions.delete(taskId)
        callbacks.onError(error)
      }
    })

    this.subscriptions.set(taskId, unsubscribe)
  }

  /**
   * 取消订阅
   */
  unsubscribe(taskId: string): void {
    const unsubscribe = this.subscriptions.get(taskId)
    if (unsubscribe) {
      unsubscribe()
      this.subscriptions.delete(taskId)
    }
  }

  /**
   * 取消所有订阅
   */
  unsubscribeAll(): void {
    this.subscriptions.forEach((unsubscribe) => unsubscribe())
    this.subscriptions.clear()
  }

  /**
   * 获取活跃订阅数
   */
  get activeCount(): number {
    return this.subscriptions.size
  }

  /**
   * 检查是否有指定任务的订阅
   */
  has(taskId: string): boolean {
    return this.subscriptions.has(taskId)
  }
}

// 全局任务管理器实例
export const taskManager = new TaskManager()
