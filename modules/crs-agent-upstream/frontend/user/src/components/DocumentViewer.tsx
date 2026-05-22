import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { ChevronLeft, ChevronRight, Info, FileText, Search, X } from 'lucide-react'
import { buildDocumentViewUrl } from '../utils/documentViewUrl'

/**
 * 文档查看器 - iframe 弹窗方式查看文档
 * 在当前页面内嵌入查看器，关闭后返回对话
 */

export interface DocumentViewerProps {
  /** 文档标题 */
  title: string
  /** 文档 URL（原始URL） */
  picFolderUrl: string
  /** URL 类型：pic_folder 需要 pdf-loader 转换，wps_page/circuit_page/raw_pdf 直接用 iframe */
  urlType?: string
  /** 打开 PDF 时定位到指定页码，页码从 1 开始 */
  initialPage?: number
  /** 图内搜索增强，仅支持已解析电路图文档 */
  circuitSearch?: CircuitDocumentSearchConfig
  /** 关闭令牌，防止旧关闭影响新打开 */
  closeToken: string
  /** 关闭回调 */
  onClose: (token?: string) => void
}

export interface CircuitDocumentSearchConfig {
  enabled: boolean
  viewerToken?: string
  keyword?: string
  hits?: CircuitDocumentSearchHit[]
  activeHitId?: string
}

export interface CircuitDocumentSearchHit {
  hit_id: string
  page_index?: number
  page_number: number
  points?: string
  bbox_px?: number[]
  highlight_boxes_px?: number[][]
  matched_text?: string
  snippet?: string
  context?: string
}

interface CircuitViewerSearchResponse {
  keyword?: string
  total_matches?: number
  positioned_match_count?: number
  results?: CircuitDocumentSearchHit[]
}

function normalizePoints(value: unknown): string {
  const rawParts = typeof value === 'string'
    ? value.split(',')
    : Array.isArray(value)
      ? value.flat()
      : null
  if (!rawParts) {
    return ''
  }
  const parts = rawParts
    .map((part) => Number(part))
    .filter((part) => Number.isFinite(part))
  if (parts.length < 4 || parts.length % 4 !== 0) {
    return ''
  }
  if (parts.some((part) => part < 0 || part > 1)) {
    return ''
  }
  return parts.map((part) => {
    const text = part.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
    return text || '0'
  }).join(',')
}

function pointsFromHit(hit?: CircuitDocumentSearchHit | null): string {
  if (!hit) return ''
  return normalizePoints(hit.points)
}

function hitLabel(hit?: CircuitDocumentSearchHit | null): string {
  if (!hit) return ''
  return String(hit.matched_text || hit.snippet || hit.context || '').trim()
}

function findInitialHitIndex(hits: CircuitDocumentSearchHit[], activeHitId?: string): number {
  if (!hits.length || !activeHitId) {
    return 0
  }
  const index = hits.findIndex((hit) => hit.hit_id === activeHitId)
  return index >= 0 ? index : 0
}

function getStableViewportHeight(): string {
  if (typeof window === 'undefined') {
    return '100vh'
  }
  const height = Math.round(window.innerHeight || document.documentElement.clientHeight || 0)
  return height > 0 ? `${height}px` : '100vh'
}

interface VisualViewportFrame {
  constrained: boolean
  width: number
}

function getVisualViewportFrame(): VisualViewportFrame {
  if (typeof window === 'undefined' || !window.visualViewport) {
    return { constrained: false, width: 0 }
  }

  const visualWidth = Math.round(window.visualViewport.width || 0)
  const layoutWidth = Math.round(window.innerWidth || document.documentElement.clientWidth || 0)
  const constrained = visualWidth >= 240 && visualWidth <= 768 && layoutWidth - visualWidth > 24
  return { constrained, width: visualWidth }
}

export default function DocumentViewer({
  title,
  picFolderUrl,
  urlType,
  initialPage,
  circuitSearch,
  closeToken,
  onClose,
}: DocumentViewerProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState(false)
  const [searchKeyword, setSearchKeyword] = useState(circuitSearch?.keyword || '')
  const [searchHits, setSearchHits] = useState<CircuitDocumentSearchHit[]>(circuitSearch?.hits || [])
  const [activeHitIndex, setActiveHitIndex] = useState(() => findInitialHitIndex(circuitSearch?.hits || [], circuitSearch?.activeHitId))
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchError, setSearchError] = useState('')
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const closeTimerRef = useRef<number | null>(null)
  const idleCallbackRef = useRef<number | null>(null)
  const originalOverflowRef = useRef<string>('')
  const viewportWidthRef = useRef(typeof window === 'undefined' ? 0 : window.innerWidth)
  const initialSearchRequestedRef = useRef(false)
  const [stableViewportHeight, setStableViewportHeight] = useState(getStableViewportHeight)
  const [visualViewportFrame, setVisualViewportFrame] = useState(getVisualViewportFrame)

  // 生成安全访问 URL：pic_folder 类型需要 pdf-loader 转换，其他类型直接使用原始 URL
  const activeHit = searchHits[activeHitIndex] || null
  const circuitSearchEnabled = Boolean(circuitSearch?.enabled)
  const viewUrl = buildDocumentViewUrl({
    picFolderUrl,
    urlType,
    initialPage: activeHit?.page_number || initialPage,
    points: pointsFromHit(activeHit),
  })
  const hitCount = searchHits.length
  const activeHitLabel = hitLabel(activeHit)
  const shouldConstrainToVisualViewport = visualViewportFrame.constrained
  const viewerStyle = circuitSearchEnabled || shouldConstrainToVisualViewport
    ? ({
        '--doc-viewer-stable-height': stableViewportHeight,
        '--doc-viewer-visual-width': shouldConstrainToVisualViewport ? `${visualViewportFrame.width}px` : undefined,
      } as CSSProperties)
    : undefined

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

  // 移动端拖拽时 100dvh 可能随浏览器视口变化抖动，锁定打开时高度避免 iframe 内部重排跳动。
  useEffect(() => {
    if (!circuitSearchEnabled) return

    const updateStableHeightForViewportChange = () => {
      const nextWidth = window.innerWidth
      if (Math.abs(nextWidth - viewportWidthRef.current) < 40) {
        return
      }
      viewportWidthRef.current = nextWidth
      setStableViewportHeight(getStableViewportHeight())
    }

    window.addEventListener('resize', updateStableHeightForViewportChange)
    window.addEventListener('orientationchange', updateStableHeightForViewportChange)
    window.visualViewport?.addEventListener('resize', updateStableHeightForViewportChange)
    return () => {
      window.removeEventListener('resize', updateStableHeightForViewportChange)
      window.removeEventListener('orientationchange', updateStableHeightForViewportChange)
      window.visualViewport?.removeEventListener('resize', updateStableHeightForViewportChange)
    }
  }, [circuitSearchEnabled])

  useEffect(() => {
    const updateVisualViewportFrame = () => {
      setVisualViewportFrame((current) => {
        const next = getVisualViewportFrame()
        return current.constrained === next.constrained && current.width === next.width ? current : next
      })
    }

    updateVisualViewportFrame()
    window.visualViewport?.addEventListener('resize', updateVisualViewportFrame)
    window.addEventListener('resize', updateVisualViewportFrame)
    window.addEventListener('orientationchange', updateVisualViewportFrame)
    return () => {
      window.visualViewport?.removeEventListener('resize', updateVisualViewportFrame)
      window.removeEventListener('resize', updateVisualViewportFrame)
      window.removeEventListener('orientationchange', updateVisualViewportFrame)
    }
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const preventSelection = (event: Event) => {
      event.preventDefault()
    }
    container.addEventListener('contextmenu', preventSelection, true)
    container.addEventListener('selectstart', preventSelection, true)
    container.addEventListener('dragstart', preventSelection, true)
    container.addEventListener('copy', preventSelection, true)
    container.addEventListener('cut', preventSelection, true)
    return () => {
      container.removeEventListener('contextmenu', preventSelection, true)
      container.removeEventListener('selectstart', preventSelection, true)
      container.removeEventListener('dragstart', preventSelection, true)
      container.removeEventListener('copy', preventSelection, true)
      container.removeEventListener('cut', preventSelection, true)
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

  const preventDocumentSelection = useCallback((event: React.SyntheticEvent) => {
    event.preventDefault()
  }, [])

  const runCircuitSearch = useCallback(async () => {
    const token = String(circuitSearch?.viewerToken || '').trim()
    const keyword = searchKeyword.trim()
    if (!keyword) {
      return
    }
    if (!token) {
      setSearchError('当前调试链接缺少 viewer token，暂不能调用图内搜索')
      return
    }

    setSearchLoading(true)
    setSearchError('')
    try {
      const response = await fetch(`/chat/api/circuit-body-search/viewer/${encodeURIComponent(token)}/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({ keyword, limit: 200 }),
      })
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json() as CircuitViewerSearchResponse
      const nextHits = Array.isArray(data.results) ? data.results : []
      setSearchHits(nextHits)
      setActiveHitIndex(0)
      if (!nextHits.length) {
        setSearchError('未找到图内命中')
      }
    } catch {
      setSearchError('图内搜索暂不可用')
    } finally {
      setSearchLoading(false)
    }
  }, [circuitSearch?.viewerToken, searchKeyword])

  const goToHit = useCallback((delta: number) => {
    setActiveHitIndex((current) => {
      if (!searchHits.length) return 0
      return (current + delta + searchHits.length) % searchHits.length
    })
  }, [searchHits.length])

  useEffect(() => {
    if (!circuitSearchEnabled || initialSearchRequestedRef.current) {
      return
    }
    if (!String(circuitSearch?.viewerToken || '').trim() || !searchKeyword.trim()) {
      return
    }
    const hasInitialHitsWithoutPoints = searchHits.length > 0 && searchHits.some((hit) => !pointsFromHit(hit))
    const hasPositionedHit = searchHits.some((hit) => Boolean(pointsFromHit(hit)))
    if (!hasInitialHitsWithoutPoints || hasPositionedHit) {
      return
    }
    initialSearchRequestedRef.current = true
    void runCircuitSearch()
  }, [circuitSearch?.viewerToken, circuitSearchEnabled, runCircuitSearch, searchHits, searchKeyword])

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
    <div
      className={`doc-viewer-backdrop${circuitSearchEnabled ? ' doc-viewer-backdrop--circuit' : ''}${closing ? ' closing' : ''}`}
      onClick={circuitSearchEnabled ? undefined : handleBackdropClick}
      onContextMenu={preventDocumentSelection}
      onCopy={preventDocumentSelection}
      onCut={preventDocumentSelection}
      onDragStart={preventDocumentSelection}
      onSelect={preventDocumentSelection}
      onSelectCapture={preventDocumentSelection}
      style={viewerStyle}
    >
      <div
        ref={containerRef}
        className={`doc-viewer-container${circuitSearchEnabled ? ' doc-viewer-container--circuit' : ''}${shouldConstrainToVisualViewport ? ' doc-viewer-container--visual-viewport' : ''}`}
        onClick={(event) => event.stopPropagation()}
        onContextMenu={preventDocumentSelection}
        onDragStart={preventDocumentSelection}
      >
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

        {circuitSearchEnabled && (
          <div className="doc-viewer-circuit-toolbar">
            <form
              className="doc-viewer-circuit-search"
              onSubmit={(event) => {
                event.preventDefault()
                void runCircuitSearch()
              }}
            >
              <div className="doc-viewer-circuit-search-field">
                <Search size={15} />
                <input
                  value={searchKeyword}
                  onChange={(event) => setSearchKeyword(event.target.value)}
                  placeholder="搜索电路图内文字"
                  aria-label="搜索电路图内文字"
                />
              </div>
              <button type="submit" disabled={searchLoading || !searchKeyword.trim()}>
                {searchLoading ? '搜索中' : '搜索'}
              </button>
            </form>
            <div className="doc-viewer-circuit-status">
              {hitCount > 0 ? (
                <span>{activeHitIndex + 1}/{hitCount} · 第 {activeHit?.page_number || '-'} 页{activeHitLabel ? ` · ${activeHitLabel}` : ''}</span>
              ) : (
                <span>{searchError || '输入关键词后可在图内定位'}</span>
              )}
            </div>
          </div>
        )}

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

        {circuitSearchEnabled && (
          <div className="doc-viewer-circuit-nav">
            <button type="button" onClick={() => goToHit(-1)} disabled={hitCount <= 1}>
              <ChevronLeft size={18} />
              上一处
            </button>
            <div className="doc-viewer-circuit-nav-count" aria-live="polite">
              {hitCount > 0 ? `第${activeHitIndex + 1}项/共${hitCount}项` : '第0项/共0项'}
            </div>
            <button type="button" onClick={() => goToHit(1)} disabled={hitCount <= 1}>
              下一处
              <ChevronRight size={18} />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
