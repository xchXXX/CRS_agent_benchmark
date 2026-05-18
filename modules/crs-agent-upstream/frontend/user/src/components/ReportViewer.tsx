/**
 * 诊断报告查看器 - 添加返回按钮
 */

import { useCallback, useEffect, useRef, useState } from 'react'

interface ReportViewerProps {
  reportUrl: string
  closeToken: string
  onClose: (token?: string) => void
}

export default function ReportViewer({ reportUrl, closeToken, onClose }: ReportViewerProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [closing, setClosing] = useState(false)
  const closeTimerRef = useRef<number | null>(null)
  const idleCallbackRef = useRef<number | null>(null)
  const originalOverflowRef = useRef<string>('')

  // 组件挂载时输出日志
  useEffect(() => {
    console.log('=== ReportViewer 组件挂载 ===')
    console.log('报告 URL:', reportUrl)
    console.log('================================')
  }, [reportUrl])

  // 统一的关闭处理函数 - 解决 iframe 焦点问题和事件穿透问题
  const handleClose = useCallback((e?: React.MouseEvent | React.TouchEvent) => {
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
        console.log('[ReportViewer] ESC 键关闭')
        handleClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [closing, handleClose])

  // 阻止背景滚动
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

  return (
    <div className={`report-viewer-overlay${closing ? ' closing' : ''}`}>
      {/* 简单的返回按钮 */}
      <button
        className="report-viewer-close-btn"
        onClick={handleClose}
        onTouchEnd={handleClose}
        title="关闭报告 (ESC)"
      >
        ✕
      </button>

      {/* 全屏 iframe */}
      <iframe
        ref={iframeRef}
        className="report-viewer-iframe"
        src={reportUrl}
        title="诊断报告"
      />
    </div>
  )
}
