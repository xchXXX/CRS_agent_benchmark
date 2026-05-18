/**
 * JSBridge 工具类
 * 用于 H5 与原生 APP 的通信
 */

// APP 提供的 API 接口类型定义
declare global {
  interface Window {
    api?: {
      closeWin: () => void
      [key: string]: any
    }
  }
}

/**
 * JSBridge 工具类
 */
export const jsBridge = {
  /**
   * 关闭当前 WebView
   * 调用 APP 提供的 api.closeWin() 方法
   */
  closeWebView() {
    console.log('[JSBridge] 尝试关闭 WebView')

    try {
      if (window.api && typeof window.api.closeWin === 'function') {
        window.api.closeWin()
        console.log('[JSBridge] 已调用 api.closeWin()')
      } else {
        console.warn('[JSBridge] window.api.closeWin 方法不存在')
        // 降级方案：使用浏览器返回
        if (window.history.length > 1) {
          window.history.back()
        }
      }
    } catch (error) {
      console.error('[JSBridge] 关闭页面失败:', error)
    }
  },

  /**
   * 检测是否在 APP 的 WebView 中
   * 判断依据：原生桥接对象存在 或 URL 中包含 from=app 参数
   */
  isInApp(): boolean {
    const hasBridge = typeof window.api !== 'undefined' && typeof window.api.closeWin === 'function'
    const urlParams = new URLSearchParams(window.location.search)
    const hasFromApp = urlParams.get('from') === 'app'
    return hasBridge || hasFromApp
  }
}

export default jsBridge
