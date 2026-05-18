/**
 * 阿里云 OSS 上传工具
 *
 * 后端用阿里云 AK/SK 生成短期 POST policy，前端只拿 policy 直传 OSS，
 * 避免把长期密钥暴露到浏览器。
 */

import { getStoredToken } from './tokenValidator'

interface ImageOssUploadPolicy {
  success: boolean
  access_id: string
  policy: string
  signature: string
  key: string
  host: string
  url: string
  delete_token?: string | null
  max_image_mb?: number
}

export interface UploadedOssImage {
  url: string
  objectKey: string
  uploadSessionId?: string | null
  deleteToken?: string | null
}

const getImageUploadPolicy = async ({
  file,
  sessionId,
}: {
  file: File | Blob
  sessionId?: string | null
}): Promise<ImageOssUploadPolicy> => {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const appToken = getStoredToken()
  if (appToken) {
    headers['x-app-token'] = appToken
  }

  const response = await fetch('/chat/api/image/oss-upload-policy', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      filename: file instanceof File ? file.name : 'image.jpg',
      content_type: file.type || 'image/jpeg',
      session_id: sessionId || undefined,
    }),
  })

  if (!response.ok) {
    let message = `获取 OSS 上传凭证失败: ${response.status}`
    try {
      const payload = await response.json()
      message = payload?.detail || payload?.message || message
    } catch {
      // ignore
    }
    throw new Error(message)
  }

  const policy = await response.json()
  if (!policy?.success || !policy.access_id || !policy.policy || !policy.signature || !policy.key || !policy.host) {
    throw new Error('OSS 上传凭证响应不完整')
  }
  return policy
}

/**
 * 上传文件到 OSS
 * @param name OSS 对象路径名
 * @param file 文件对象
 * @returns [error, result]
 */
export const uploadFileToOss = async ({
  name,
  file,
  sessionId,
}: {
  name: string
  file: File | Blob
  sessionId?: string | null
}): Promise<[Error | null, { url: string; name: string; deleteToken?: string | null } | undefined]> => {
  try {
    const policy = await getImageUploadPolicy({ file, sessionId })
    const formData = new FormData()
    formData.append('key', policy.key)
    formData.append('policy', policy.policy)
    formData.append('OSSAccessKeyId', policy.access_id)
    formData.append('Signature', policy.signature)
    formData.append('success_action_status', '200')
    formData.append('Content-Type', file.type || 'application/octet-stream')
    formData.append('file', file, file instanceof File ? file.name : name)

    const response = await fetch(policy.host, {
      method: 'POST',
      body: formData,
    })
    if (!response.ok) {
      throw new Error(`OSS 上传失败: ${response.status}`)
    }

    return [null, { url: policy.url, name: policy.key, deleteToken: policy.delete_token }]
  } catch (err) {
    return [err instanceof Error ? err : new Error('Failed to put file'), undefined]
  }
}

/**
 * 生成 OSS 上传路径
 * 格式：chat_images/<timestamp>_<random>.<ext>
 */
export const generateOssPath = (fileName: string): string => {
  const timestamp = Date.now()
  const random = Math.random().toString(36).substring(2, 8)
  const ext = fileName.split('.').pop() || 'jpg'
  return `chat_images/${timestamp}_${random}.${ext}`
}

/**
 * 压缩图片并转换为 JPEG
 * 与原版保持一致的压缩逻辑
 */
export const compressImage = async (
  file: File,
  maxWidth: number = 1920,
  quality: number = 0.8
): Promise<File> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onload = (event) => {
      const img = new Image()

      img.onload = () => {
        const canvas = document.createElement('canvas')
        let { width, height } = img

        // 按比例缩放
        if (width > maxWidth) {
          height = Math.round((height * maxWidth) / width)
          width = maxWidth
        }

        canvas.width = width
        canvas.height = height

        const ctx = canvas.getContext('2d')
        if (!ctx) {
          reject(new Error('Failed to get canvas context'))
          return
        }

        ctx.drawImage(img, 0, 0, width, height)

        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error('Failed to compress image'))
              return
            }

            const compressedFile = new File(
              [blob],
              file.name.replace(/\.[^.]+$/, '.jpg'),
              { type: 'image/jpeg' }
            )

            resolve(compressedFile)
          },
          'image/jpeg',
          quality
        )
      }

      img.onerror = () => reject(new Error('Failed to load image'))
      img.src = event.target?.result as string
    }

    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsDataURL(file)
  })
}

/**
 * 上传图片（完整流程：压缩 -> 上传）
 */
export const uploadImage = async (
  file: File,
  sessionId?: string | null
): Promise<[Error | null, UploadedOssImage | undefined]> => {
  try {
    // 1. 压缩图片
    const compressedFile = await compressImage(file)

    // 2. 生成 OSS 路径
    const ossPath = generateOssPath(compressedFile.name)

    // 3. 上传到 OSS
    const [error, result] = await uploadFileToOss({
      name: ossPath,
      file: compressedFile,
      sessionId,
    })

    if (error || !result) {
      return [error || new Error('Upload failed'), undefined]
    }

    // 4. 返回稳定访问 URL 和对象 key
    return [null, { url: result.url, objectKey: result.name, uploadSessionId: sessionId || null, deleteToken: result.deleteToken }]
  } catch (err) {
    return [err instanceof Error ? err : new Error('Upload failed'), undefined]
  }
}

export const requestDeleteOssImages = async ({
  sessionId,
  objects,
  reason = 'new_search',
}: {
  sessionId?: string | null
  reason?: string
  objects: Array<{ key: string; deleteToken?: string | null }>
}): Promise<void> => {
  const validObjects = objects
    .filter(item => item.key && item.deleteToken)
    .slice(0, 50)
    .map(item => ({
      key: item.key,
      delete_token: item.deleteToken,
    }))
  if (validObjects.length === 0) return

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const appToken = getStoredToken()
  if (appToken) {
    headers['x-app-token'] = appToken
  }

  await fetch('/chat/api/image/oss-delete-objects', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      session_id: sessionId || undefined,
      reason,
      objects: validObjects,
    }),
    keepalive: true,
  })
}
