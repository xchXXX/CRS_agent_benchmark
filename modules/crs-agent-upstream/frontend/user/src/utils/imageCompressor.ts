/**
 * 图片压缩工具
 *
 * 将图片压缩到指定大小以下，保持合理画质
 * 针对移动端优化，降低内存占用
 */

export interface CompressOptions {
  maxSizeMB?: number      // 最大文件大小（MB），默认 3（适配百炼API限制）
  maxWidthOrHeight?: number // 最大宽高，默认 1600（移动端友好）
  initialQuality?: number   // 初始压缩质量，默认 0.85
}

// 检测是否移动设备
const isMobile = (): boolean => {
  return /iPhone|iPad|iPod|Android/i.test(navigator.userAgent)
}

/**
 * 压缩图片
 *
 * @param file 原始图片文件
 * @param options 压缩选项
 * @returns 压缩后的 Blob
 */
export async function compressImage(
  file: File,
  options: CompressOptions = {}
): Promise<Blob> {
  const {
    // 百炼 API 限制约 4MB，这里设置 3MB 留有余量
    maxSizeMB = 3,
    // 移动端使用更小的尺寸以节省内存和加快传输
    maxWidthOrHeight = isMobile() ? 1000 : 1200,
    initialQuality = 0.8
  } = options

  const maxSizeBytes = maxSizeMB * 1024 * 1024

  // 如果文件已经小于限制，直接返回
  if (file.size <= maxSizeBytes) {
    console.log(`图片大小 ${(file.size / 1024 / 1024).toFixed(2)}MB，无需压缩`)
    return file
  }

  console.log(`开始压缩图片: ${(file.size / 1024 / 1024).toFixed(2)}MB -> 目标 ${maxSizeMB}MB`)

  try {
    // 加载图片 - 使用 createImageBitmap 更高效（如果支持）
    const img = await loadImageEfficient(file)

    // 计算缩放尺寸
    let width = img.width
    let height = img.height

    if (width > maxWidthOrHeight || height > maxWidthOrHeight) {
      const ratio = Math.min(maxWidthOrHeight / width, maxWidthOrHeight / height)
      width = Math.round(width * ratio)
      height = Math.round(height * ratio)
      console.log(`缩放尺寸: ${img.width}x${img.height} -> ${width}x${height}`)
    }

    // 创建离屏 Canvas（如果支持）以减少主线程阻塞
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d', { alpha: false })
    if (!ctx) {
      throw new Error('无法创建 Canvas 上下文')
    }

    // 设置白色背景（避免透明图片问题）
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(0, 0, width, height)

    // 绘制图片
    if (img instanceof ImageBitmap) {
      ctx.drawImage(img, 0, 0, width, height)
      img.close() // 释放 ImageBitmap 内存
    } else {
      ctx.drawImage(img, 0, 0, width, height)
    }

    // 尝试不同的压缩质量
    const qualities = [initialQuality, 0.75, 0.6, 0.5, 0.4]

    for (const quality of qualities) {
      const blob = await canvasToBlob(canvas, 'image/jpeg', quality)
      console.log(`质量 ${quality}: ${(blob.size / 1024 / 1024).toFixed(2)}MB`)

      if (blob.size <= maxSizeBytes) {
        console.log(`压缩完成: ${(file.size / 1024 / 1024).toFixed(2)}MB -> ${(blob.size / 1024 / 1024).toFixed(2)}MB`)
        // 清理 Canvas
        canvas.width = 0
        canvas.height = 0
        return blob
      }
    }

    // 如果最低质量还是超限，继续缩小尺寸
    let scale = 0.75
    while (scale > 0.25) {
      const newWidth = Math.round(width * scale)
      const newHeight = Math.round(height * scale)

      canvas.width = newWidth
      canvas.height = newHeight
      ctx.fillStyle = '#FFFFFF'
      ctx.fillRect(0, 0, newWidth, newHeight)

      // 重新加载图片以获取最佳质量
      const imgAgain = await loadImageEfficient(file)
      if (imgAgain instanceof ImageBitmap) {
        ctx.drawImage(imgAgain, 0, 0, newWidth, newHeight)
        imgAgain.close()
      } else {
        ctx.drawImage(imgAgain, 0, 0, newWidth, newHeight)
      }

      const blob = await canvasToBlob(canvas, 'image/jpeg', 0.5)
      console.log(`缩放 ${scale}: ${newWidth}x${newHeight}, ${(blob.size / 1024 / 1024).toFixed(2)}MB`)

      if (blob.size <= maxSizeBytes) {
        console.log(`压缩完成: ${(file.size / 1024 / 1024).toFixed(2)}MB -> ${(blob.size / 1024 / 1024).toFixed(2)}MB`)
        canvas.width = 0
        canvas.height = 0
        return blob
      }

      scale -= 0.15
    }

    // 最后手段：返回最小尺寸的结果
    console.warn('无法压缩到目标大小，返回最小可能的结果')
    const finalBlob = await canvasToBlob(canvas, 'image/jpeg', 0.4)
    canvas.width = 0
    canvas.height = 0
    return finalBlob
  } catch (error) {
    console.error('图片压缩失败:', error)
    // 压缩失败时返回原文件，让后端处理
    return file
  }
}

/**
 * 高效加载图片 - 优先使用 createImageBitmap
 */
async function loadImageEfficient(file: File): Promise<ImageBitmap | HTMLImageElement> {
  // 优先使用 createImageBitmap（更高效，不阻塞主线程）
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file)
    } catch {
      // 降级到传统方式
      console.log('createImageBitmap 失败，使用传统方式加载')
    }
  }

  // 传统方式
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(file)

    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片加载失败'))
    }
    img.src = url
  })
}

/**
 * Canvas 转 Blob
 */
function canvasToBlob(
  canvas: HTMLCanvasElement,
  type: string,
  quality: number
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob)
        } else {
          reject(new Error('Canvas 转换失败'))
        }
      },
      type,
      quality
    )
  })
}

/**
 * 获取图片预览 URL
 */
export function getImagePreviewUrl(file: File | Blob): string {
  return URL.createObjectURL(file)
}

/**
 * 释放预览 URL
 */
export function revokeImagePreviewUrl(url: string): void {
  URL.revokeObjectURL(url)
}
