/**
 * 图片上传按钮组件
 *
 * 在输入框旁边的"+"按钮，点击后显示下拉菜单
 * 支持：上传图片 / 拍照
 *
 * 解决移动端拍照刷新问题：
 * - 使用 Portal 将 input 渲染到 body，避免在 form 内部
 * - 防止事件冒泡
 * - 配合 IndexedDB 保存图片，页面恢复后可继续处理
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Plus, Image, Camera } from 'lucide-react'

interface ImageUploadButtonProps {
  onImageSelect: (files: File[]) => void
  /** 在打开文件选择器之前调用，返回 true 继续，返回 false 取消 */
  onBeforeSelect?: () => boolean | Promise<boolean>
  disabled?: boolean
  disabledReason?: string
  maxFiles?: number
}

export default function ImageUploadButton({
  onImageSelect,
  onBeforeSelect,
  disabled = false,
  disabledReason,
  maxFiles = 3
}: ImageUploadButtonProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)

  // 保存回调函数的引用
  const callbackRef = useRef<((files: File[]) => void) | null>(null)

  // 更新回调引用
  useEffect(() => {
    callbackRef.current = onImageSelect
  }, [onImageSelect])

  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }
    if (menuOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [menuOpen])

  // 处理文件选择
  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault()
    e.stopPropagation()

    const selectedFiles = Array.from(e.target.files || []).slice(0, maxFiles)
    if (selectedFiles.length > 0 && callbackRef.current) {
      // 延迟处理，给浏览器时间完成文件选择
      const callback = callbackRef.current
      setTimeout(() => {
        callback(selectedFiles)
      }, 150)
    }

    // 清空 input 以便再次选择同一文件
    setTimeout(() => {
      if (e.target) {
        e.target.value = ''
      }
    }, 300)
  }, [maxFiles])

  const handleUploadClick = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setMenuOpen(false)

    // 先检测是否可以继续
    if (onBeforeSelect) {
      const canProceed = await onBeforeSelect()
      if (!canProceed) return
    }

    // 延迟触发，确保菜单关闭
    setTimeout(() => {
      fileInputRef.current?.click()
    }, 100)
  }, [onBeforeSelect])

  const handleCameraClick = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setMenuOpen(false)

    // 先检测是否可以继续
    if (onBeforeSelect) {
      const canProceed = await onBeforeSelect()
      if (!canProceed) return
    }

    setTimeout(() => {
      cameraInputRef.current?.click()
    }, 100)
  }, [onBeforeSelect])

  const handleMenuToggle = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setMenuOpen(prev => !prev)
  }, [])

  // 将 input 渲染到 body 中，避免在 form 内部导致的问题
  const fileInputs = createPortal(
    <>
      {/* 隐藏的文件输入 - 相册 */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={handleFileChange}
        style={{
          position: 'fixed',
          top: '-9999px',
          left: '-9999px',
          opacity: 0,
          pointerEvents: 'none',
        }}
        tabIndex={-1}
        aria-hidden="true"
      />

      {/* 隐藏的文件输入 - 相机 */}
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        onChange={handleFileChange}
        style={{
          position: 'fixed',
          top: '-9999px',
          left: '-9999px',
          opacity: 0,
          pointerEvents: 'none',
        }}
        tabIndex={-1}
        aria-hidden="true"
      />
    </>,
    document.body
  )

  return (
    <>
      {fileInputs}

      <div className="image-upload-container" ref={menuRef}>
        <button
          type="button"
          className={`image-upload-btn ${menuOpen ? 'active' : ''}`}
          onClick={handleMenuToggle}
          disabled={disabled}
          title={disabledReason || "上传图片辅助识别"}
          aria-label={disabledReason || "上传图片辅助识别"}
        >
          <Plus size={20} />
        </button>

        {/* 下拉菜单 */}
        {menuOpen && (
          <div className="image-upload-menu">
            <button
              type="button"
              className="image-upload-option"
              onClick={handleUploadClick}
            >
              <span className="option-icon">
                <Image size={16} />
              </span>
              <span className="option-text">上传图片辅助识别</span>
            </button>
            <button
              type="button"
              className="image-upload-option"
              onClick={handleCameraClick}
            >
              <span className="option-icon">
                <Camera size={16} />
              </span>
              <span className="option-text">拍照辅助识别</span>
            </button>
          </div>
        )}
      </div>
    </>
  )
}
