export interface FrontendRuntimeConfig {
  eruda_enabled?: boolean
  webview_debug_enabled?: boolean
  webview_debug_url?: string
  webview_debug_viewer_token?: string
}

let debugConsoleReady = false

function getUrlErudaOverride(): boolean | null {
  const value = new URLSearchParams(window.location.search).get('eruda')
  if (value === null) return null
  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase())
}

export async function fetchFrontendRuntimeConfig(): Promise<FrontendRuntimeConfig> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), 3000)

  try {
    const response = await fetch('/chat/api/frontend/runtime-config', {
      headers: {
        Accept: 'application/json',
      },
      cache: 'no-store',
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    return response.json()
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export async function setupDebugConsole(): Promise<void> {
  if (debugConsoleReady) return

  try {
    const urlOverride = getUrlErudaOverride()
    if (urlOverride === false) return
    if (urlOverride === true) {
      await initEruda()
      return
    }

    const runtimeConfig = await fetchFrontendRuntimeConfig()
    if (!runtimeConfig.eruda_enabled) return

    await initEruda()
  } catch (error) {
    console.warn('[DebugConsole] Eruda 初始化跳过:', error)
  }
}

async function initEruda(): Promise<void> {
  const erudaModule = await import('eruda')
  erudaModule.default.init()
  erudaModule.default.hide()
  debugConsoleReady = true
}
