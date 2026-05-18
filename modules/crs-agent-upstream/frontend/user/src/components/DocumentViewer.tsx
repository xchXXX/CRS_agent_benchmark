import { useCallback, useEffect, useRef, useState } from 'react'
import { Info, FileText, X } from 'lucide-react'
import { getSafeVisitUrl } from '../utils/urlUtils'

/**
 * 文档查看器 - iframe 弹窗方式查看文档
 * 在当前页面内嵌入查看器，关闭后返回对话
 */

export interface DocumentViewerProps {
  /** 文档标题 */
  title: string
  /** 文档 URL（原始URL） */
  picFolderUrl: string
  /** URL 类型：pic_folder 需要 pdf-loader 转换，wps_page/circuit_page 直接用 iframe */
  urlType?: string
  /** 关闭令牌，防止旧关闭影响新打开 */
  closeToken: string
  /** 关闭回调 */
  onClose: (token?: string) => void
}

export default function DocumentViewer({ title, picFolderUrl, urlType, closeToken, onClose }: DocumentViewerProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const closeTimerRef = useRef<number | null>(null)
  const idleCallbackRef = useRef<number | null>(null)
  const originalOverflowRef = useRef<string>('')

  // 生成安全访问 URL：pic_folder 类型需要 pdf-loader 转换，其他类型直接使用原始 URL
  const viewUrl = urlType === 'pic_folder' || !urlType
    ? getSafeVisitUrl(picFolderUrl)
    : picFolderUrl

  // 统一的关闭处理函数 - 解决 iframe 焦点问题和事件穿透问题
  const handleClose = useCallback((e?: React.MouseEvent | React.TouchEvent) => {
    // 阻止事件穿透到下层元素
    if (e) {
      e.preventDefault()
      e.stopPropagation()
    }

    // 防止重复触发
    if (closing) return

    setClosing(true)

    // 先恢复滚动与释放 iframe 资源，再异步卸载
    document.body.style.overflow = originalOverflowRef.current

    const iframe = iframeRef.current
    if (iframe) {
      try {
        iframe.contentWindow?.stop()
      } catch (_) {
        // ignore
      }
      try {
        iframe.src = 'about:blank'
      } catch (_) {
        // ignore
      }
    }

    const doClose = () => onClose(closeToken)
    const requestIdle = (window as Window & {
      requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number
    }).requestIdleCallback

    if (requestIdle) {
      idleCallbackRef.current = requestIdle(() => doClose(), { timeout: 500 })
    } else {
      closeTimerRef.current = window.setTimeout(doClose, 200)
    }
  }, [closing, closeToken, onClose])

  // ESC 键关闭
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !closing) {
        handleClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [closing, handleClose])

  // 禁止背景滚动
  useEffect(() => {
    originalOverflowRef.current = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current)
      }
      const cancelIdle = (window as Window & { cancelIdleCallback?: (handle: number) => void }).cancelIdleCallback
      if (cancelIdle && idleCallbackRef.current !== null) {
        cancelIdle(idleCallbackRef.current)
      }
      document.body.style.overflow = originalOverflowRef.current
    }
  }, [])

  // iframe 加载完成
  const handleIframeLoad = useCallback(() => {
    setLoading(false)
  }, [])

  // iframe 加载错误
  const handleIframeError = useCallback(() => {
    setLoading(false)
    setError('文档加载失败，请稍后重试')
  }, [])

  // 点击背景关闭
  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget && !closing) {
      handleClose(e)
    }
  }, [closing, handleClose])

  if (!viewUrl) {
    return (
      <div className={`doc-viewer-backdrop${closing ? ' closing' : ''}`} onClick={handleBackdropClick}>
        <div className="doc-viewer-error">
          <div className="doc-viewer-error-icon">
            <Info size={32} />
          </div>
          <p>该文档暂无访问链接</p>
          <button className="doc-viewer-close-btn" onClick={handleClose}>
            关闭
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`doc-viewer-backdrop${closing ? ' closing' : ''}`} onClick={handleBackdropClick}>
      <div className="doc-viewer-container">
        {/* 顶部标题栏 */}
        <div className="doc-viewer-header">
          <div className="doc-viewer-title">
            <FileText size={18} />
            <span className="doc-viewer-title-text">{title}</span>
          </div>
          <button
            className="doc-viewer-close-icon"
            onClick={handleClose}
            onTouchEnd={handleClose}
            title="关闭 (ESC)"
          >
            <X size={20} />
          </button>
        </div>

        {/* 内容区域 */}
        <div className="doc-viewer-content">
          {loading && (
            <div className="doc-viewer-loading">
              <div className="doc-viewer-spinner"></div>
              <p>文档加载中...</p>
            </div>
          )}

          {error && (
            <div className="doc-viewer-error-inline">
              <p>{error}</p>
              <button onClick={handleClose}>关闭</button>
            </div>
          )}

          <iframe
            ref={iframeRef}
            src={viewUrl}
            className="doc-viewer-iframe"
            onLoad={handleIframeLoad}
            onError={handleIframeError}
            title={title}
            style={{ opacity: loading ? 0 : 1 }}
          />
        </div>
      </div>
    </div>
  )
}
