/**
 * IndexedDB 图片存储工具
 *
 * 用于解决移动端拍照导致页面刷新的问题
 * 在选择图片后立即保存到 IndexedDB，页面恢复后可继续处理
 */

const DB_NAME = 'doc_search_images'
const DB_VERSION = 1
const STORE_NAME = 'pending_images'

interface PendingImage {
  id: string
  file: Blob
  fileName: string
  fileType: string
  timestamp: number
}

class ImageStorage {
  private db: IDBDatabase | null = null
  private initPromise: Promise<void> | null = null

  /**
   * 初始化数据库
   */
  async init(): Promise<void> {
    if (this.db) return
    if (this.initPromise) return this.initPromise

    this.initPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION)

      request.onerror = () => {
        console.error('[ImageStorage] 打开数据库失败:', request.error)
        reject(request.error)
      }

      request.onsuccess = () => {
        this.db = request.result
        console.log('[ImageStorage] 数据库已打开')
        resolve()
      }

      request.onupgradeneeded = (event) => {
        const db = (event.target as IDBOpenDBRequest).result
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'id' })
          console.log('[ImageStorage] 创建对象存储')
        }
      }
    })

    return this.initPromise
  }

  /**
   * 保存待处理的图片
   */
  async savePendingImage(file: File): Promise<string> {
    await this.init()
    if (!this.db) throw new Error('数据库未初始化')

    const id = `pending_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`
    const pendingImage: PendingImage = {
      id,
      file: file,
      fileName: file.name,
      fileType: file.type,
      timestamp: Date.now(),
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([STORE_NAME], 'readwrite')
      const store = transaction.objectStore(STORE_NAME)
      const request = store.put(pendingImage)

      request.onsuccess = () => {
        console.log('[ImageStorage] 图片已保存:', id)
        resolve(id)
      }

      request.onerror = () => {
        console.error('[ImageStorage] 保存图片失败:', request.error)
        reject(request.error)
      }
    })
  }

  /**
   * 获取待处理的图片
   */
  async getPendingImage(id: string): Promise<File | null> {
    await this.init()
    if (!this.db) return null

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([STORE_NAME], 'readonly')
      const store = transaction.objectStore(STORE_NAME)
      const request = store.get(id)

      request.onsuccess = () => {
        const result = request.result as PendingImage | undefined
        if (result) {
          // 将 Blob 转换回 File
          const file = new File([result.file], result.fileName, { type: result.fileType })
          resolve(file)
        } else {
          resolve(null)
        }
      }

      request.onerror = () => {
        console.error('[ImageStorage] 获取图片失败:', request.error)
        reject(request.error)
      }
    })
  }

  /**
   * 获取所有待处理的图片
   */
  async getAllPendingImages(): Promise<{ id: string; file: File; timestamp: number }[]> {
    await this.init()
    if (!this.db) return []

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([STORE_NAME], 'readonly')
      const store = transaction.objectStore(STORE_NAME)
      const request = store.getAll()

      request.onsuccess = () => {
        const results = (request.result as PendingImage[]) || []
        const files = results.map(item => ({
          id: item.id,
          file: new File([item.file], item.fileName, { type: item.fileType }),
          timestamp: item.timestamp,
        }))
        resolve(files)
      }

      request.onerror = () => {
        console.error('[ImageStorage] 获取所有图片失败:', request.error)
        reject(request.error)
      }
    })
  }

  /**
   * 删除已处理的图片
   */
  async deletePendingImage(id: string): Promise<void> {
    await this.init()
    if (!this.db) return

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([STORE_NAME], 'readwrite')
      const store = transaction.objectStore(STORE_NAME)
      const request = store.delete(id)

      request.onsuccess = () => {
        console.log('[ImageStorage] 图片已删除:', id)
        resolve()
      }

      request.onerror = () => {
        console.error('[ImageStorage] 删除图片失败:', request.error)
        reject(request.error)
      }
    })
  }

  /**
   * 清理过期的图片（超过 5 分钟的）
   */
  async cleanupExpired(): Promise<void> {
    await this.init()
    if (!this.db) return

    const expireTime = 5 * 60 * 1000 // 5 分钟
    const now = Date.now()

    try {
      const allImages = await this.getAllPendingImages()
      for (const item of allImages) {
        if (now - item.timestamp > expireTime) {
          await this.deletePendingImage(item.id)
          console.log('[ImageStorage] 清理过期图片:', item.id)
        }
      }
    } catch (error) {
      console.error('[ImageStorage] 清理过期图片失败:', error)
    }
  }

  /**
   * 清空所有待处理图片
   */
  async clearAll(): Promise<void> {
    await this.init()
    if (!this.db) return

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([STORE_NAME], 'readwrite')
      const store = transaction.objectStore(STORE_NAME)
      const request = store.clear()

      request.onsuccess = () => {
        console.log('[ImageStorage] 已清空所有图片')
        resolve()
      }

      request.onerror = () => {
        console.error('[ImageStorage] 清空失败:', request.error)
        reject(request.error)
      }
    })
  }
}

// 导出单例
export const imageStorage = new ImageStorage()
