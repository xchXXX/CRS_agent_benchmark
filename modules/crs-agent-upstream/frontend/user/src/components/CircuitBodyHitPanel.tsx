import { ChevronDown, ExternalLink, FileSearch, ImageOff, Loader2 } from 'lucide-react'
import type { KeyboardEvent, MouseEvent } from 'react'
import { useEffect, useRef, useState } from 'react'
import { buildDocumentViewUrl } from '@/utils/documentViewUrl'

export interface CircuitBodyBestHit {
  hit_id: string
  candidate_id?: string
  page_index: number
  page_number: number
  matched_text?: string
  snippet?: string
  context?: string
  nearby_ocr_text?: string
  highlight_boxes_px?: number[][]
  source_hit_ids?: string[]
  display_rank?: number
  score?: number
  confidence?: 'high' | 'medium' | 'low'
  reason?: string
  viewer_token?: string
  preview_image_url?: string
}

export interface CircuitBodySearch {
  status?: string
  pdf_id?: string
  keyword?: string
  source_pdf_url?: string
  viewer_token?: string
  viewer_url_type?: string
  raw_hit_count?: number
  page_hit_count?: number
  region_candidate_count?: number
  display_hit_count?: number
  more_hits_count?: number
  best_hit?: CircuitBodyBestHit
  top_hits?: CircuitBodyBestHit[]
  rerank_source?: string
}

interface CircuitBodyHitPanelProps {
  bodySearch?: CircuitBodySearch
  hit?: CircuitBodyBestHit
  expanded: boolean
  rank?: number
  isPrimary?: boolean
  resolveDocumentAccess?: () => Promise<{ url: string; urlType?: string } | null>
  onToggle: () => void
  onOpenDocument: () => void
}

export default function CircuitBodyHitPanel({
  bodySearch,
  hit,
  expanded,
  rank,
  isPrimary = false,
  resolveDocumentAccess,
  onToggle,
  onOpenDocument,
}: CircuitBodyHitPanelProps) {
  const bestHit = hit || bodySearch?.best_hit
  const [previewUrl, setPreviewUrl] = useState('')
  const [frameState, setFrameState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle')
  const frameLoadTimerRef = useRef<number | null>(null)
  const resolveDocumentAccessRef = useRef(resolveDocumentAccess)

  useEffect(() => {
    resolveDocumentAccessRef.current = resolveDocumentAccess
  }, [resolveDocumentAccess])

  useEffect(() => {
    if (frameLoadTimerRef.current !== null) {
      window.clearTimeout(frameLoadTimerRef.current)
      frameLoadTimerRef.current = null
    }

    if (!expanded || !bestHit) {
      setFrameState('idle')
      setPreviewUrl('')
      return
    }

    let cancelled = false
    frameLoadTimerRef.current = window.setTimeout(() => {
      if (!cancelled) {
        setFrameState('error')
      }
      frameLoadTimerRef.current = null
    }, 20000)

    setFrameState('loading')
    setPreviewUrl('')

    const loadPreviewUrl = async () => {
      const access = await resolveDocumentAccessRef.current?.()
      if (cancelled) {
        return
      }
      const nextPreviewUrl = access?.url
        ? buildDocumentViewUrl({
          picFolderUrl: access.url,
          urlType: access.urlType,
          initialPage: bestHit.page_number,
        })
        : ''
      if (!nextPreviewUrl) {
        if (frameLoadTimerRef.current !== null) {
          window.clearTimeout(frameLoadTimerRef.current)
          frameLoadTimerRef.current = null
        }
        setFrameState('error')
        return
      }
      setPreviewUrl(nextPreviewUrl)
    }

    loadPreviewUrl().catch(() => {
      if (cancelled) {
        return
      }
      if (frameLoadTimerRef.current !== null) {
        window.clearTimeout(frameLoadTimerRef.current)
        frameLoadTimerRef.current = null
      }
      setFrameState('error')
    })

    return () => {
      cancelled = true
      if (frameLoadTimerRef.current !== null) {
        window.clearTimeout(frameLoadTimerRef.current)
        frameLoadTimerRef.current = null
      }
    }
  }, [bestHit?.hit_id, bestHit?.page_number, expanded])

  if (bodySearch?.status !== 'hit' || !bestHit) {
    return null
  }

  const pageHitText = bodySearch.page_hit_count && bodySearch.page_hit_count > 1
    ? `命中 ${bodySearch.page_hit_count} 页`
    : '图内精确命中'
  const snippet = (bestHit?.snippet || bestHit?.matched_text || bestHit?.nearby_ocr_text || bodySearch.keyword || '').trim()
  const titleText = bestHit?.matched_text || bestHit?.snippet || bodySearch.keyword || '图内命中'
  const summaryText = snippet || titleText
  const confidenceText = bestHit?.confidence === 'high'
    ? '高匹配'
    : bestHit?.confidence === 'low'
      ? '低匹配'
      : '中匹配'

  const handleToggle = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.stopPropagation()
    onToggle()
  }

  const handleOpenDocument = (event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
    onOpenDocument()
  }

  const handlePreviewKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }
    handleOpenDocument(event)
  }

  const handleFrameLoad = () => {
    if (frameLoadTimerRef.current !== null) {
      window.clearTimeout(frameLoadTimerRef.current)
      frameLoadTimerRef.current = null
    }
    setFrameState('loaded')
  }

  return (
    <div className={`circuit-hit-panel${expanded ? ' is-expanded' : ''}`}>
      <button
        type="button"
        className="circuit-hit-summary"
        onClick={handleToggle}
        aria-expanded={expanded}
      >
        <span className="circuit-hit-icon">
          <FileSearch size={14} strokeWidth={2.4} />
        </span>
        <span className="circuit-hit-copy">
          <span className="circuit-hit-line">
            {isPrimary && <span className="circuit-hit-priority">首选</span>}
            <span className="circuit-hit-status">{titleText}</span>
            <span className="circuit-hit-dot" />
            <span>第 {bestHit.page_number} 页</span>
            <span className="circuit-hit-dot" />
            <span>{confidenceText}</span>
          </span>
          {snippet && <span className="circuit-hit-snippet">“{snippet}”</span>}
        </span>
        <ChevronDown size={15} className="circuit-hit-chevron" strokeWidth={2.5} />
      </button>

      {expanded && (
        <div className="circuit-hit-detail" onClick={(event) => event.stopPropagation()}>
          <div
            className="circuit-hit-preview"
            onClick={handleOpenDocument}
            onKeyDown={handlePreviewKeyDown}
            role="button"
            tabIndex={0}
            aria-label={`打开第 ${bestHit.page_number} 页图内命中位置`}
          >
            <div className="circuit-hit-preview-topbar">
              <span className="circuit-hit-preview-badge">{titleText}</span>
              <span className="circuit-hit-preview-page">第 {bestHit.page_number} 页</span>
            </div>
            {!previewUrl && (
              <div className="circuit-hit-error">
                <ImageOff size={20} />
                <span>局部图暂不可用</span>
              </div>
            )}
            {previewUrl && frameState === 'loading' && (
              <div className="circuit-hit-loading">
                <Loader2 size={18} />
                <span>局部图加载中</span>
              </div>
            )}
            {previewUrl && frameState === 'error' && (
              <div className="circuit-hit-error">
                <ImageOff size={20} />
                <span>局部图暂不可用</span>
              </div>
            )}
            {previewUrl && frameState !== 'error' && (
              <iframe
                key={previewUrl}
                src={previewUrl}
                title={`第 ${bestHit.page_number} 页图内命中预览`}
                className="circuit-hit-webview"
                style={{ opacity: frameState === 'loaded' ? 1 : 0 }}
                onLoad={handleFrameLoad}
              />
            )}
            <div className="circuit-hit-preview-bottombar">
              <span className="circuit-hit-preview-caption">{summaryText}</span>
            </div>
          </div>
          <div className="circuit-hit-footer">
            <span>{rank ? `候选 ${rank} · ` : ''}第 {bestHit.page_number} 页 · {pageHitText}</span>
            <button type="button" className="circuit-hit-open" onClick={handleOpenDocument}>
              <ExternalLink size={14} strokeWidth={2.3} />
              定位查看
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
