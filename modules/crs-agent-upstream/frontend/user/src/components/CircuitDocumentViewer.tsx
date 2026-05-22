import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
  type PointerEvent,
} from 'react'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
  Menu,
  RotateCcw,
  Search,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import {
  getCircuitViewerMetadata,
  getCircuitViewerPageImageUrl,
  searchCircuitViewer,
  type CircuitViewerHit,
  type CircuitViewerMetadata,
  type CircuitViewerPageInfo,
  type CircuitViewerSearchResponse,
} from '@/services/api'
import './CircuitDocumentViewer.css'

interface CircuitDocumentViewerProps {
  title: string
  token: string
  initialKeyword?: string
  initialHitId?: string
  initialPageIndex?: number
  fallbackPdfUrl?: string
  fallbackPage?: number
  closeToken: string
  onClose: (token?: string) => void
}

type LoadState = 'idle' | 'loading' | 'loaded' | 'error'
type PointerPoint = { x: number; y: number }
type ZoomAnchor = {
  ratioX: number
  ratioY: number
  viewportX: number
  viewportY: number
}

const DEFAULT_PAGE_WIDTH = 1000
const DEFAULT_PAGE_HEIGHT = 1414
const HIT_BOUND_MARGIN_RATIO = 1.02
const MIN_ZOOM = 0.75
const MAX_ZOOM = 4
const DEFAULT_ZOOM = 1
const DEFAULT_FOCUS_ZOOM = 1.8
const ZOOM_STEP = 0.25
const EMPTY_VIEWER_HITS: CircuitViewerHit[] = []

function isCanceledError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return true
  }
  if (typeof error === 'object' && error !== null) {
    const value = error as { name?: string; code?: string }
    return value.name === 'CanceledError' || value.code === 'ERR_CANCELED'
  }
  return false
}

function validBox(value?: number[]): value is [number, number, number, number] {
  return Array.isArray(value) &&
    value.length === 4 &&
    value.every((part) => Number.isFinite(part)) &&
    value[2] > value[0] &&
    value[3] > value[1]
}

function appendPdfPageFragment(url: string, page?: number): string {
  const pageNumber = Math.floor(Number(page || 0))
  if (!url || pageNumber < 1) {
    return url
  }
  const [baseUrl, rawHash = ''] = url.split('#')
  const pageFragment = `page=${pageNumber}`
  if (!rawHash) {
    return `${baseUrl}#${pageFragment}`
  }
  if (/(^|&)page=\d+/.test(rawHash)) {
    return `${baseUrl}#${rawHash.replace(/(^|&)page=\d+/, `$1${pageFragment}`)}`
  }
  return `${baseUrl}#${rawHash}&${pageFragment}`
}

function pageInfoFor(metadata: CircuitViewerMetadata | null, pageIndex: number): CircuitViewerPageInfo {
  const page = metadata?.pages?.find((item) => item.page_index === pageIndex)
  return {
    page_index: pageIndex,
    page_number: pageIndex + 1,
    width_px: page?.width_px && page.width_px > 0 ? page.width_px : 0,
    height_px: page?.height_px && page.height_px > 0 ? page.height_px : 0,
  }
}

function firstKnownPageSize(metadata: CircuitViewerMetadata | null): { width: number; height: number } {
  const page = metadata?.pages?.find((item) => item.width_px > 0 && item.height_px > 0)
  return {
    width: page?.width_px || 0,
    height: page?.height_px || 0,
  }
}

function hitBounds(hits: CircuitViewerHit[]): { maxX: number; maxY: number } {
  let maxX = 0
  let maxY = 0
  for (const hit of hits) {
    if (!validBox(hit.bbox_px)) continue
    maxX = Math.max(maxX, hit.bbox_px[2])
    maxY = Math.max(maxY, hit.bbox_px[3])
  }
  return { maxX, maxY }
}

function expandPageSizeForHits(
  width: number,
  height: number,
  bounds: { maxX: number; maxY: number }
): { width: number; height: number } {
  const baseWidth = width > 0 ? width : DEFAULT_PAGE_WIDTH
  const baseHeight = height > 0 ? height : DEFAULT_PAGE_HEIGHT
  const requiredWidth = bounds.maxX * HIT_BOUND_MARGIN_RATIO
  const requiredHeight = bounds.maxY * HIT_BOUND_MARGIN_RATIO
  const scale = Math.max(requiredWidth / baseWidth, requiredHeight / baseHeight, 1)
  return {
    width: baseWidth * scale,
    height: baseHeight * scale,
  }
}

function percent(value: number, total: number): number {
  if (!Number.isFinite(value) || total <= 0) {
    return 0
  }
  return Math.min(Math.max((value / total) * 100, 0), 100)
}

function clampZoom(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_ZOOM
  }
  return Math.min(Math.max(value, MIN_ZOOM), MAX_ZOOM)
}

function clampUnit(value: number): number {
  if (!Number.isFinite(value)) {
    return 0.5
  }
  return Math.min(Math.max(value, 0), 1)
}

function pointerDistance(points: PointerPoint[]): number {
  if (points.length < 2) {
    return 0
  }
  return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y)
}

function pointerMidpoint(points: PointerPoint[]): PointerPoint | null {
  if (points.length < 2) {
    return null
  }
  return {
    x: (points[0].x + points[1].x) / 2,
    y: (points[0].y + points[1].y) / 2,
  }
}

export default function CircuitDocumentViewer({
  title,
  token,
  initialKeyword,
  initialHitId,
  initialPageIndex,
  fallbackPdfUrl,
  fallbackPage,
  closeToken,
  onClose,
}: CircuitDocumentViewerProps) {
  const [metadata, setMetadata] = useState<CircuitViewerMetadata | null>(null)
  const [metadataState, setMetadataState] = useState<LoadState>('loading')
  const [metadataError, setMetadataError] = useState('')
  const [searchQuery, setSearchQuery] = useState(initialKeyword || '')
  const [searchResponse, setSearchResponse] = useState<CircuitViewerSearchResponse | null>(null)
  const [searchState, setSearchState] = useState<LoadState>('idle')
  const [searchError, setSearchError] = useState('')
  const [currentPageIndex, setCurrentPageIndex] = useState(Math.max(Number(initialPageIndex || 0), 0))
  const [selectedHitIndex, setSelectedHitIndex] = useState(-1)
  const [pageImageState, setPageImageState] = useState<LoadState>('loading')
  const [pageImageError, setPageImageError] = useState('')
  const [isSidebarOpen, setIsSidebarOpen] = useState(true)
  const [naturalImageSize, setNaturalImageSize] = useState({ width: 0, height: 0 })
  const [zoom, setZoom] = useState(DEFAULT_ZOOM)
  const [scrollerViewportWidth, setScrollerViewportWidth] = useState(0)
  const [isDraggingPage, setIsDraggingPage] = useState(false)

  const scrollerRef = useRef<HTMLElement | null>(null)
  const pageSurfaceRef = useRef<HTMLDivElement | null>(null)
  const dragStateRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    scrollLeft: number
    scrollTop: number
  } | null>(null)
  const activePointersRef = useRef<Map<number, PointerPoint>>(new Map())
  const pinchStateRef = useRef<{ distance: number; zoom: number } | null>(null)
  const pendingZoomAnchorRef = useRef<ZoomAnchor | null>(null)
  const metadataAbortRef = useRef<AbortController | null>(null)
  const searchAbortRef = useRef<AbortController | null>(null)
  const initializedTokenRef = useRef('')
  const closeTimerRef = useRef<number | null>(null)

  const hits = searchResponse?.results || EMPTY_VIEWER_HITS
  const selectedHit = selectedHitIndex >= 0 ? hits[selectedHitIndex] : null
  const initialHighlightHits = useMemo<CircuitViewerHit[]>(() => {
    if (searchResponse || !metadata?.initial_highlight_boxes_px?.length) {
      return []
    }
    return metadata.initial_highlight_boxes_px
      .filter(validBox)
      .map((box, index) => ({
        hit_id: `${metadata.initial_hit_id || 'initial'}_${index}`,
        page_index: metadata.initial_page_index,
        page_number: metadata.initial_page_number,
        bbox_px: box,
        matched_text: metadata.keyword || initialKeyword || '',
        context: '',
      }))
  }, [initialKeyword, metadata, searchResponse])
  const currentPageHits = useMemo(() => {
    const pageHits = hits.filter((hit) => hit.page_index === currentPageIndex)
    if (pageHits.length > 0) {
      return pageHits
    }
    return initialHighlightHits.filter((hit) => hit.page_index === currentPageIndex)
  }, [currentPageIndex, hits, initialHighlightHits])
  const pageInfo = useMemo(
    () => pageInfoFor(metadata, currentPageIndex),
    [metadata, currentPageIndex]
  )
  const documentPageSize = useMemo(
    () => firstKnownPageSize(metadata),
    [metadata]
  )
  const currentHitBounds = useMemo(
    () => hitBounds(currentPageHits),
    [currentPageHits]
  )
  const coordinatePageSize = useMemo(() => expandPageSizeForHits(
    pageInfo.width_px || documentPageSize.width || naturalImageSize.width || DEFAULT_PAGE_WIDTH,
    pageInfo.height_px || documentPageSize.height || naturalImageSize.height || DEFAULT_PAGE_HEIGHT,
    currentHitBounds
  ), [
    currentHitBounds,
    documentPageSize.height,
    documentPageSize.width,
    naturalImageSize.height,
    naturalImageSize.width,
    pageInfo.height_px,
    pageInfo.width_px,
  ])
  const effectivePageWidth = coordinatePageSize.width
  const effectivePageHeight = coordinatePageSize.height
  const pageSurfaceWidth = useMemo(() => {
    const availableWidth = scrollerViewportWidth > 0
      ? Math.max(scrollerViewportWidth - 28, 280)
      : 360
    const baseWidth = Math.min(availableWidth, 640)
    return Math.round(baseWidth * zoom)
  }, [scrollerViewportWidth, zoom])
  const pageHitCounts = useMemo(() => {
    const map = new Map<number, number>()
    for (const item of searchResponse?.page_summary || []) {
      map.set(item.page_index, item.match_count)
    }
    return map
  }, [searchResponse])
  const pageCount = Math.max(metadata?.total_pages || 0, currentPageIndex + 1, 1)
  const pageIndexes = useMemo(
    () => Array.from({ length: pageCount }, (_, index) => index),
    [pageCount]
  )
  const pageImageUrl = useMemo(
    () => getCircuitViewerPageImageUrl(token, currentPageIndex),
    [token, currentPageIndex]
  )

  const focusOnHit = useCallback((hit: CircuitViewerHit | null) => {
    if (!hit || hit.page_index !== currentPageIndex || !validBox(hit.bbox_px)) {
      return
    }
    const scroller = scrollerRef.current
    const surface = pageSurfaceRef.current
    if (!scroller || !surface || effectivePageWidth <= 0) {
      return
    }

    const [x1, y1, x2, y2] = hit.bbox_px
    const renderedScaleX = surface.clientWidth / effectivePageWidth
    const renderedScaleY = surface.clientHeight / effectivePageHeight
    const centerX = ((x1 + x2) / 2) * renderedScaleX
    const centerY = ((y1 + y2) / 2) * renderedScaleY
    const left = Math.max(0, surface.offsetLeft + centerX - scroller.clientWidth / 2)
    const top = Math.max(0, surface.offsetTop + centerY - scroller.clientHeight / 2)
    scroller.scrollTo({ left, top, behavior: 'smooth' })
  }, [currentPageIndex, effectivePageHeight, effectivePageWidth])

  const buildZoomAnchor = useCallback((clientPoint?: PointerPoint | null): ZoomAnchor | null => {
    const scroller = scrollerRef.current
    const surface = pageSurfaceRef.current
    if (!scroller || !surface || surface.clientWidth <= 0 || surface.clientHeight <= 0) {
      return null
    }

    const scrollerRect = scroller.getBoundingClientRect()
    const viewportX = clientPoint ? clientPoint.x - scrollerRect.left : scroller.clientWidth / 2
    const viewportY = clientPoint ? clientPoint.y - scrollerRect.top : scroller.clientHeight / 2
    const surfaceX = scroller.scrollLeft + viewportX - surface.offsetLeft
    const surfaceY = scroller.scrollTop + viewportY - surface.offsetTop
    return {
      ratioX: clampUnit(surfaceX / surface.clientWidth),
      ratioY: clampUnit(surfaceY / surface.clientHeight),
      viewportX,
      viewportY,
    }
  }, [])

  const updateZoom = useCallback((
    nextZoom: number | ((value: number) => number),
    clientPoint?: PointerPoint | null
  ) => {
    pendingZoomAnchorRef.current = buildZoomAnchor(clientPoint)
    setZoom((current) => {
      const next = clampZoom(typeof nextZoom === 'function' ? nextZoom(current) : nextZoom)
      if (Math.abs(next - current) < 0.001) {
        pendingZoomAnchorRef.current = null
        return current
      }
      return next
    })
  }, [buildZoomAnchor])

  const selectHit = useCallback((nextIndex: number) => {
    if (!hits.length) {
      setSelectedHitIndex(-1)
      return
    }
    const normalizedIndex = (nextIndex + hits.length) % hits.length
    const hit = hits[normalizedIndex]
    setSelectedHitIndex(normalizedIndex)
    if (hit.page_index !== currentPageIndex) {
      setCurrentPageIndex(hit.page_index)
    } else {
      window.setTimeout(() => focusOnHit(hit), 40)
    }
  }, [currentPageIndex, focusOnHit, hits])

  const runSearch = useCallback(async (keyword: string, preferredHitId?: string) => {
    const trimmed = keyword.trim()
    searchAbortRef.current?.abort()
    if (!trimmed) {
      setSearchResponse(null)
      setSelectedHitIndex(-1)
      setSearchState('idle')
      setSearchError('')
      return
    }

    const controller = new AbortController()
    searchAbortRef.current = controller
    setSearchState('loading')
    setSearchError('')
    try {
      const response = await searchCircuitViewer(token, trimmed, controller.signal)
      setSearchResponse(response)
      setSearchState('loaded')

      const preferredIndex = preferredHitId
        ? response.results.findIndex((hit) => hit.hit_id === preferredHitId)
        : -1
      const pagePreferredIndex = preferredIndex >= 0
        ? preferredIndex
        : response.results.findIndex((hit) => hit.page_index === (metadata?.initial_page_index ?? currentPageIndex))
      const nextIndex = pagePreferredIndex >= 0 ? pagePreferredIndex : (response.results.length ? 0 : -1)
      setSelectedHitIndex(nextIndex)
      const nextHit = nextIndex >= 0 ? response.results[nextIndex] : null
      if (nextHit) {
        setZoom((current) => Math.max(current, DEFAULT_FOCUS_ZOOM))
        setCurrentPageIndex(nextHit.page_index)
      }
    } catch (error) {
      if (isCanceledError(error)) return
      setSearchState('error')
      setSearchError('图内搜索失败，请稍后重试')
    }
  }, [currentPageIndex, metadata?.initial_page_index, token])

  useEffect(() => {
    return () => {
      const scroller = scrollerRef.current
      if (scroller && typeof scroller.hasPointerCapture === 'function') {
        for (const pointerId of activePointersRef.current.keys()) {
          if (scroller.hasPointerCapture(pointerId)) {
            try {
              scroller.releasePointerCapture(pointerId)
            } catch {
              // ignore
            }
          }
        }
      }
      activePointersRef.current.clear()
      dragStateRef.current = null
      pinchStateRef.current = null
      metadataAbortRef.current?.abort()
      searchAbortRef.current?.abort()
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    metadataAbortRef.current?.abort()
    const controller = new AbortController()
    metadataAbortRef.current = controller
    setMetadataState('loading')
    setMetadataError('')
    setMetadata(null)
    setSearchResponse(null)
    setSelectedHitIndex(-1)
    setZoom(DEFAULT_ZOOM)
    setIsDraggingPage(false)
    dragStateRef.current = null
    activePointersRef.current.clear()
    pinchStateRef.current = null
    pendingZoomAnchorRef.current = null
    initializedTokenRef.current = ''

    getCircuitViewerMetadata(token, controller.signal)
      .then((payload) => {
        setMetadata(payload)
        setMetadataState('loaded')
        const nextPage = typeof initialPageIndex === 'number'
          ? initialPageIndex
          : payload.initial_page_index
        setCurrentPageIndex(Math.max(nextPage || 0, 0))
        if (payload.initial_highlight_boxes_px?.length) {
          setZoom(DEFAULT_FOCUS_ZOOM)
        }
      })
      .catch((error) => {
        if (isCanceledError(error)) return
        setMetadataState('error')
        setMetadataError('图内查看信息加载失败')
      })

    return () => controller.abort()
  }, [initialKeyword, initialPageIndex, token])

  useEffect(() => {
    if (!metadata || initializedTokenRef.current === token) return
    initializedTokenRef.current = token
    const keyword = (initialKeyword || metadata.keyword || '').trim()
    setSearchQuery(keyword)
    if (keyword) {
      void runSearch(keyword, initialHitId || metadata.initial_hit_id)
    }
  }, [initialHitId, initialKeyword, metadata, runSearch, token])

  useEffect(() => {
    setPageImageState('loading')
    setPageImageError('')
    setNaturalImageSize({ width: 0, height: 0 })
  }, [pageImageUrl])

  useEffect(() => {
    if (pageImageState !== 'loaded') return
    const focusTarget = selectedHit || currentPageHits[0] || null
    const timer = window.setTimeout(() => focusOnHit(focusTarget), 80)
    return () => window.clearTimeout(timer)
  }, [currentPageHits, focusOnHit, pageImageState, selectedHit])

  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller) return

    const updateWidth = () => {
      setScrollerViewportWidth(scroller.clientWidth)
    }

    updateWidth()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateWidth)
      return () => window.removeEventListener('resize', updateWidth)
    }

    const observer = new ResizeObserver(updateWidth)
    observer.observe(scroller)
    return () => observer.disconnect()
  }, [])

  useLayoutEffect(() => {
    const anchor = pendingZoomAnchorRef.current
    const scroller = scrollerRef.current
    const surface = pageSurfaceRef.current
    if (!anchor || !scroller || !surface) {
      return
    }

    pendingZoomAnchorRef.current = null
    const maxLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth)
    const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight)
    const left = surface.offsetLeft + anchor.ratioX * surface.clientWidth - anchor.viewportX
    const top = surface.offsetTop + anchor.ratioY * surface.clientHeight - anchor.viewportY
    scroller.scrollLeft = Math.min(Math.max(left, 0), maxLeft)
    scroller.scrollTop = Math.min(Math.max(top, 0), maxTop)
  }, [pageSurfaceWidth, zoom])

  useEffect(() => {
    if (!hits.length || selectedHitIndex < 0) return
    const pageIndexesToPreload = new Set<number>()
    for (const index of [selectedHitIndex - 1, selectedHitIndex, selectedHitIndex + 1]) {
      const hit = hits[(index + hits.length) % hits.length]
      if (hit) {
        pageIndexesToPreload.add(hit.page_index)
      }
    }
    pageIndexesToPreload.delete(currentPageIndex)
    pageIndexesToPreload.forEach((pageIndex) => {
      const image = new Image()
      image.decoding = 'async'
      image.src = getCircuitViewerPageImageUrl(token, pageIndex)
    })
  }, [currentPageIndex, hits, selectedHitIndex, token])

  const handleClose = useCallback(() => {
    closeTimerRef.current = window.setTimeout(() => onClose(closeToken), 120)
  }, [closeToken, onClose])

  const handleBackdropClick = useCallback((event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      handleClose()
    }
  }, [handleClose])

  const handleSearchSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void runSearch(searchQuery)
  }, [runSearch, searchQuery])

  const handlePageSelect = useCallback((pageIndex: number) => {
    setCurrentPageIndex(pageIndex)
    const firstHitOnPage = hits.findIndex((hit) => hit.page_index === pageIndex)
    if (firstHitOnPage >= 0) {
      setSelectedHitIndex(firstHitOnPage)
    } else {
      setSelectedHitIndex(-1)
    }
  }, [hits])

  const handlePagePointerDown = useCallback((event: PointerEvent<HTMLElement>) => {
    const scroller = scrollerRef.current
    const surface = pageSurfaceRef.current
    if (!scroller || !surface || !surface.contains(event.target as Node)) {
      return
    }
    if (event.pointerType === 'mouse' && event.button !== 0) {
      return
    }

    activePointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    if (typeof scroller.setPointerCapture === 'function') {
      try {
        scroller.setPointerCapture(event.pointerId)
      } catch {
        // Pointer capture is a best-effort enhancement; dragging still works without it.
      }
    }
    if (activePointersRef.current.size >= 2) {
      const distance = pointerDistance(Array.from(activePointersRef.current.values()))
      pinchStateRef.current = distance > 0 ? { distance, zoom } : null
      dragStateRef.current = null
      setIsDraggingPage(false)
      event.preventDefault()
      return
    }

    dragStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: scroller.scrollLeft,
      scrollTop: scroller.scrollTop,
    }
    setIsDraggingPage(true)
    event.preventDefault()
  }, [zoom])

  const handlePagePointerMove = useCallback((event: PointerEvent<HTMLElement>) => {
    const scroller = scrollerRef.current
    if (activePointersRef.current.has(event.pointerId)) {
      activePointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    }

    if (activePointersRef.current.size >= 2 && pinchStateRef.current) {
      const points = Array.from(activePointersRef.current.values())
      const distance = pointerDistance(points)
      if (distance > 0) {
        updateZoom(
          pinchStateRef.current.zoom * (distance / pinchStateRef.current.distance),
          pointerMidpoint(points)
        )
      }
      event.preventDefault()
      return
    }

    const dragState = dragStateRef.current
    if (!scroller || !dragState || dragState.pointerId !== event.pointerId) {
      return
    }

    scroller.scrollLeft = dragState.scrollLeft - (event.clientX - dragState.startX)
    scroller.scrollTop = dragState.scrollTop - (event.clientY - dragState.startY)
    event.preventDefault()
  }, [updateZoom])

  const finishPageDrag = useCallback((event: PointerEvent<HTMLElement>) => {
    const scroller = scrollerRef.current
    if (
      scroller &&
      typeof scroller.hasPointerCapture === 'function' &&
      scroller.hasPointerCapture(event.pointerId)
    ) {
      try {
        scroller.releasePointerCapture(event.pointerId)
      } catch {
        // ignore
      }
    }
    activePointersRef.current.delete(event.pointerId)
    if (activePointersRef.current.size < 2) {
      pinchStateRef.current = null
    }
    if (dragStateRef.current?.pointerId === event.pointerId) {
      dragStateRef.current = null
      setIsDraggingPage(false)
    }
  }, [])

  const openFallbackPdf = useCallback(() => {
    if (!fallbackPdfUrl) return
    window.open(appendPdfPageFragment(fallbackPdfUrl, fallbackPage), '_blank', 'noopener,noreferrer')
  }, [fallbackPage, fallbackPdfUrl])

  return (
    <div className="circuit-viewer-backdrop" onClick={handleBackdropClick}>
      <section className="circuit-viewer-shell" role="dialog" aria-modal="true" aria-label="图内搜索查看器">
        <header className="circuit-viewer-topbar">
          <button
            type="button"
            className="circuit-viewer-icon-button"
            onClick={() => setIsSidebarOpen((value) => !value)}
            aria-label="切换页码"
          >
            <Menu size={20} />
          </button>
          <div className="circuit-viewer-title">
            <FileText size={16} />
            <span>{metadata?.filename || title}</span>
          </div>
          <button
            type="button"
            className="circuit-viewer-icon-button"
            onClick={handleClose}
            aria-label="关闭"
          >
            <X size={20} />
          </button>
        </header>

        <form className="circuit-viewer-searchbar" onSubmit={handleSearchSubmit}>
          <Search size={18} className="circuit-viewer-search-icon" />
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="搜索文档内文字"
            disabled={metadataState === 'loading'}
          />
          <button type="submit" disabled={searchState === 'loading' || !searchQuery.trim()}>
            {searchState === 'loading' ? <Loader2 size={16} className="circuit-viewer-spin" /> : '搜索'}
          </button>
        </form>

        <div className="circuit-viewer-body">
          <aside className={`circuit-viewer-sidebar${isSidebarOpen ? ' is-open' : ''}`}>
            {pageIndexes.map((pageIndex) => {
              const count = pageHitCounts.get(pageIndex) || 0
              const isCurrent = pageIndex === currentPageIndex
              return (
                <button
                  key={pageIndex}
                  type="button"
                  className={`circuit-viewer-page-tab${isCurrent ? ' is-current' : ''}`}
                  onClick={() => handlePageSelect(pageIndex)}
                >
                  <span>{pageIndex + 1}</span>
                  {count > 0 && <i>{count}</i>}
                </button>
              )
            })}
          </aside>

          <main
            className={`circuit-viewer-main${isDraggingPage ? ' is-dragging' : ''}`}
            ref={scrollerRef}
            onPointerDown={handlePagePointerDown}
            onPointerMove={handlePagePointerMove}
            onPointerUp={finishPageDrag}
            onPointerCancel={finishPageDrag}
            onPointerLeave={finishPageDrag}
          >
            {metadataState === 'loading' && (
              <div className="circuit-viewer-state">
                <Loader2 className="circuit-viewer-spin" size={24} />
                <span>正在加载图内查看器</span>
              </div>
            )}

            {metadataState === 'error' && (
              <div className="circuit-viewer-state is-error">
                <AlertTriangle size={24} />
                <span>{metadataError}</span>
                {fallbackPdfUrl && (
                  <button type="button" onClick={openFallbackPdf}>
                    打开原文
                  </button>
                )}
              </div>
            )}

            {metadataState === 'loaded' && (
              <>
                {searchState === 'error' && (
                  <div className="circuit-viewer-inline-error">
                    <AlertTriangle size={16} />
                    <span>{searchError}</span>
                  </div>
                )}
                {searchResponse && hits.length === 0 && searchState !== 'loading' && (
                  <div className="circuit-viewer-inline-empty">
                    当前文档未找到“{searchResponse.keyword}”
                  </div>
                )}
                <div className="circuit-viewer-zoom-toolbar" aria-label="页面缩放">
                  <button
                    type="button"
                    onClick={() => updateZoom((value) => value - ZOOM_STEP)}
                    disabled={zoom <= MIN_ZOOM}
                    aria-label="缩小"
                  >
                    <ZoomOut size={16} />
                  </button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <input
                    className="circuit-viewer-zoom-slider"
                    type="range"
                    min={Math.round(MIN_ZOOM * 100)}
                    max={Math.round(MAX_ZOOM * 100)}
                    step={1}
                    value={Math.round(zoom * 100)}
                    onChange={(event) => updateZoom(Number(event.currentTarget.value) / 100)}
                    aria-label="连续缩放"
                  />
                  <button
                    type="button"
                    onClick={() => updateZoom((value) => value + ZOOM_STEP)}
                    disabled={zoom >= MAX_ZOOM}
                    aria-label="放大"
                  >
                    <ZoomIn size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => updateZoom(DEFAULT_ZOOM)}
                    disabled={Math.abs(zoom - DEFAULT_ZOOM) < 0.01}
                    aria-label="重置缩放"
                  >
                    <RotateCcw size={15} />
                  </button>
                </div>
                <div className="circuit-viewer-page-wrap">
                  <div
                    ref={pageSurfaceRef}
                    className="circuit-viewer-page-surface"
                    style={{
                      aspectRatio: `${effectivePageWidth} / ${effectivePageHeight}`,
                      width: `${pageSurfaceWidth}px`,
                    }}
                  >
                    {pageImageState === 'loading' && (
                      <div className="circuit-viewer-page-loading">
                        <Loader2 className="circuit-viewer-spin" size={24} />
                        <span>页图加载中</span>
                      </div>
                    )}
                    {pageImageState === 'error' && (
                      <div className="circuit-viewer-page-loading is-error">
                        <AlertTriangle size={24} />
                        <span>{pageImageError || '页图暂不可用'}</span>
                        {fallbackPdfUrl && (
                          <button type="button" onClick={openFallbackPdf}>
                            打开原文
                          </button>
                        )}
                      </div>
                    )}
                    <img
                      src={pageImageUrl}
                      alt={`第 ${currentPageIndex + 1} 页`}
                      onLoad={(event) => {
                        setNaturalImageSize({
                          width: event.currentTarget.naturalWidth,
                          height: event.currentTarget.naturalHeight,
                        })
                        setPageImageState('loaded')
                      }}
                      onError={() => {
                        setPageImageState('error')
                        setPageImageError('页图加载失败')
                      }}
                      style={{ opacity: pageImageState === 'loaded' ? 1 : 0 }}
                    />
                    <div className="circuit-viewer-highlight-layer" aria-hidden="true">
                      {currentPageHits.map((hit) => {
                        if (!validBox(hit.bbox_px)) return null
                        const [x1, y1, x2, y2] = hit.bbox_px
                        const isSelected = selectedHit?.hit_id === hit.hit_id
                        const left = percent(x1, effectivePageWidth)
                        const top = percent(y1, effectivePageHeight)
                        const width = Math.max(percent(x2, effectivePageWidth) - left, 0)
                        const height = Math.max(percent(y2, effectivePageHeight) - top, 0)
                        return (
                          <span
                            key={hit.hit_id}
                            className={`circuit-viewer-highlight${isSelected ? ' is-selected' : ''}`}
                            style={{
                              left: `${left}%`,
                              top: `${top}%`,
                              width: `${width}%`,
                              height: `${height}%`,
                            }}
                          />
                        )
                      })}
                    </div>
                  </div>
                </div>
              </>
            )}
          </main>
        </div>

        <footer className="circuit-viewer-footer">
          <button
            type="button"
            onClick={() => selectHit(selectedHitIndex >= 0 ? selectedHitIndex - 1 : hits.length - 1)}
            disabled={!hits.length}
          >
            <ChevronLeft size={18} />
            上一项
          </button>
          <div className="circuit-viewer-hit-count">
            <strong>{hits.length ? selectedHitIndex + 1 : 0}</strong>
            <span>/</span>
            <span>共 {searchResponse?.positioned_match_count || 0} 项</span>
            {searchResponse?.truncated && <em>仅显示前 200</em>}
          </div>
          <button
            type="button"
            onClick={() => selectHit(selectedHitIndex >= 0 ? selectedHitIndex + 1 : 0)}
            disabled={!hits.length}
          >
            下一项
            <ChevronRight size={18} />
          </button>
          {fallbackPdfUrl && (
            <button type="button" className="circuit-viewer-fallback" onClick={openFallbackPdf}>
              <ExternalLink size={16} />
            </button>
          )}
        </footer>
      </section>
    </div>
  )
}
