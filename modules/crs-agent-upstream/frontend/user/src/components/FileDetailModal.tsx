import { useCallback, useEffect, useState } from 'react'

/**
 * 文档信息弹窗 - 与小程序保持一致
 * 显示文件名和关联文件ID，支持复制ID
 */

export interface FileInfo {
  title: string
  refFileId?: number | string | null
}

interface FileDetailModalProps {
  fileInfo: FileInfo | null
  onClose: () => void
}

export default function FileDetailModal({ fileInfo, onClose }: FileDetailModalProps) {
  const [copied, setCopied] = useState(false)

  // 复制关联文件ID到剪贴板
  const handleCopyId = useCallback(async () => {
    if (!fileInfo?.refFileId) return

    try {
      await navigator.clipboard.writeText(String(fileInfo.refFileId))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      // 降级方案：使用 execCommand
      const textArea = document.createElement('textarea')
      textArea.value = String(fileInfo.refFileId)
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      } catch {
        console.error('复制失败')
      }
      document.body.removeChild(textArea)
    }
  }, [fileInfo])

  // 关闭弹窗 - 点击背景
  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose()
    }
  }, [onClose])

  // ESC 键关闭
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // 重置复制状态
  useEffect(() => {
    setCopied(false)
  }, [fileInfo])

  if (!fileInfo) return null

  const refFileIdDisplay = fileInfo.refFileId ?? '无'
  const hasRefFileId = fileInfo.refFileId != null && fileInfo.refFileId !== ''

  return (
    <div className="file-info-backdrop" onClick={handleBackdropClick}>
      <div className="file-info-modal">
        {/* 标题 */}
        <div className="file-info-header">
          <div className="file-info-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
          </div>
          <h3>文档信息</h3>
        </div>

        {/* 内容 */}
        <div className="file-info-content">
          <div className="file-info-row">
            <span className="file-info-label">文件名</span>
            <span className="file-info-value file-info-title">{fileInfo.title}</span>
          </div>
          <div className="file-info-row">
            <span className="file-info-label">关联文件ID</span>
            <span className={`file-info-value ${hasRefFileId ? 'file-info-id' : 'file-info-empty'}`}>
              {refFileIdDisplay}
            </span>
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="file-info-footer">
          <button className="file-info-btn file-info-btn-secondary" onClick={onClose}>
            关闭
          </button>
          {hasRefFileId && (
            <button
              className={`file-info-btn file-info-btn-primary ${copied ? 'copied' : ''}`}
              onClick={handleCopyId}
            >
              {copied ? (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  已复制
                </>
              ) : (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                  复制ID
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
