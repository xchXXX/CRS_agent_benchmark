/**
 * Token 校验工具
 * 用于在页面加载时校验 APP 传入的 token 是否有效
 */

import axios from 'axios'
import jsBridge from './jsBridge'

const TOKEN_STORAGE_KEY = 'app_token'
const COOKIE_TOKEN_NAME = 'APP_TOKEN'

/** 记录 token 最终来源，供诊断使用 */
let lastTokenSource: string | null = null

/**
 * 获取已存储的 token
 */
export function getStoredToken(): string | null {
  return sessionStorage.getItem(TOKEN_STORAGE_KEY)
}

/**
 * 清除已存储的 token
 */
export function clearStoredToken(): void {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY)
}

/**
 * 后端 token 校验代理接口响应
 */
interface ValidateTokenResponse {
  valid: boolean
  userId?: number
  message?: string
}

/**
 * 从 Cookie 中获取 token
 * 读取名为 APP_TOKEN 的 Cookie 值
 */
function getTokenFromCookie(): string | null {
  const cookies = document.cookie
  if (!cookies) return null

  const pairs = cookies.split(';')
  for (const pair of pairs) {
    const trimmed = pair.trim()
    const eqIdx = trimmed.indexOf('=')
    if (eqIdx === -1) continue
    const name = trimmed.substring(0, eqIdx).trim()
    if (name === COOKIE_TOKEN_NAME) {
      const value = trimmed.substring(eqIdx + 1).trim()
      // Cookie 值可能被 URI 编码
      try {
        const decoded = decodeURIComponent(value)
        if (decoded) {
          console.log('[TokenValidator] 从 Cookie APP_TOKEN 获取到 token')
          return decoded
        }
      } catch {
        // 解码失败，尝试直接使用原始值
        if (value) {
          console.log('[TokenValidator] 从 Cookie APP_TOKEN 获取到 token（原始值）')
          return value
        }
      }
    }
  }
  return null
}

/**
 * 从 URL 获取 token
 * 优先读取 appToken，降级读取 app-token，再降级读取 token
 *
 * 注意：不使用 URLSearchParams，因为它会将 + 号解码为空格（form-urlencoded 规范），
 * 而 base64 token 中的 + 是有意义的字符。这里手动解析保留原始值。
 */
function getTokenFromUrl(): string | null {
  const search = window.location.search
  if (!search || search.length <= 1) return null

  const targetKeys = ['appToken', 'app-token', 'token']
  // 手动解析查询字符串
  const pairs = search.substring(1).split('&')
  for (const pair of pairs) {
    const eqIdx = pair.indexOf('=')
    if (eqIdx === -1) continue
    const key = decodeURIComponent(pair.substring(0, eqIdx))
    if (targetKeys.includes(key)) {
      // APP 对 base64 token 中的 + 号编码不一致：
      // 部分 + 被正确编码为 %2B，部分保留为字面 +。
      // WebView 按 form-urlencoded 规范将字面 + 解读为空格。
      // 由于 base64 token 中不可能出现空格，解码后将所有空格还原为 +。
      const value = decodeURIComponent(pair.substring(eqIdx + 1)).replace(/ /g, '+')
      if (value) return value
    }
  }
  return null
}

/**
 * 从 URL 中移除 token 相关参数，防止泄露
 */
export function cleanTokenFromUrl(): void {
  const url = new URL(window.location.href)
  url.searchParams.delete('appToken')
  url.searchParams.delete('app-token')
  url.searchParams.delete('token')
  window.history.replaceState(window.history.state, '', url.toString())
}

/**
 * 校验 token 是否有效
 * 通过本域后端代理转发到共轨接口，避免 CORS 跨域问题
 * @param token 待校验的 token
 * @returns Promise<boolean> token 是否有效
 * @throws 网络异常时抛出错误（区分鉴权失败和网络问题）
 */
async function validateToken(token: string): Promise<boolean> {
  console.log('[TokenValidator] 开始校验 token（通过后端代理）')

  const response = await axios.post<ValidateTokenResponse>(
    '/chat/api/legacy/validate-token',
    { token },
    { timeout: 15000 }
  )

  console.log('[TokenValidator] 校验响应:', response.data)

  if (response.data && response.data.valid) {
    console.log('[TokenValidator] Token 有效，用户ID:', response.data.userId)
    return true
  } else {
    console.warn('[TokenValidator] Token 无效:', response.data?.message)
    return false
  }
}

/**
 * 收集 Token 诊断信息（供弹窗展示）
 */
async function collectDiagnoseInfo(): Promise<Record<string, unknown>> {
  const result: Record<string, unknown> = {}

  // Token 获取结果总览
  const storedToken = getStoredToken()
  result['Token获取结果'] = storedToken ? '已获取' : '未获取'
  result['Token来源'] = lastTokenSource || '(未记录 / 未获取到token)'
  if (storedToken) {
    result['Token值(前20位)'] = storedToken.substring(0, 20) + '...'
  }

  result['当前URL'] = window.location.href

  // Cookie APP_TOKEN 详情
  const cookieToken = getTokenFromCookie()
  result['Cookie APP_TOKEN'] = cookieToken
    ? { '状态': '已找到', '值(前20位)': cookieToken.substring(0, 20) + '...' }
    : { '状态': '未找到' }

  // 手动解析 URL 参数（空格还原为 +，修复 WebView 对 base64 + 号的错误解码）
  const urlParams: Record<string, string> = {}
  const search = window.location.search
  if (search && search.length > 1) {
    const pairs = search.substring(1).split('&')
    for (const pair of pairs) {
      const eqIdx = pair.indexOf('=')
      if (eqIdx === -1) continue
      const key = decodeURIComponent(pair.substring(0, eqIdx))
      const value = decodeURIComponent(pair.substring(eqIdx + 1)).replace(/ /g, '+')
      urlParams[key] = value
    }
  }
  result['URL参数(原始)'] = Object.keys(urlParams).length > 0 ? urlParams : '(无)'

  // 显示实际提取到的 token 值
  const extractedToken = getTokenFromUrl()
  result['URL提取的token'] = extractedToken || '(未提取到)'

  const ssTokens: Record<string, string> = {}
  for (const key of ['app_token', 'appToken', 'app-token', 'token']) {
    const v = sessionStorage.getItem(key)
    if (v) ssTokens[key] = v.substring(0, 20) + '...'
  }
  result['sessionStorage'] = Object.keys(ssTokens).length > 0 ? ssTokens : '(无)'

  const lsTokens: Record<string, string> = {}
  for (const key of ['appToken', 'app-token', 'token']) {
    const v = localStorage.getItem(key)
    if (v) lsTokens[key] = v.substring(0, 20) + '...'
  }
  result['localStorage'] = Object.keys(lsTokens).length > 0 ? lsTokens : '(无)'

  result['Cookie(全部)'] = document.cookie || '(无)'
  result['UserAgent'] = navigator.userAgent

  // APICloud
  if (typeof window.api !== 'undefined') {
    const api = window.api as Record<string, unknown>
    const apiInfo: Record<string, unknown> = { '存在': true }
    for (const key of ['appParam', 'pageParam', 'wgtParam']) {
      const val = api[key]
      if (val !== undefined && val !== null) {
        if (typeof val === 'string') {
          try { apiInfo[`api.${key}`] = JSON.parse(val) } catch { apiInfo[`api.${key}`] = val }
        } else {
          apiInfo[`api.${key}`] = val
        }
      }
    }
    if (typeof api.getGlobalData === 'function') {
      const gd: Record<string, unknown> = {}
      for (const key of ['appToken', 'token', 'userInfo']) {
        try {
          const v = (api.getGlobalData as (o: { key: string }) => unknown)({ key })
          if (v !== undefined && v !== null) gd[key] = typeof v === 'string' && v.length > 60 ? v.substring(0, 60) + '...' : v
        } catch { /* ignore */ }
      }
      apiInfo['getGlobalData'] = Object.keys(gd).length > 0 ? gd : '(无)'
    }
    result['APICloud'] = apiInfo
  }

  // 后端请求头
  try {
    const resp = await fetch('/chat/api/legacy/token-diagnose')
    const data = await resp.json()
    result['后端请求头(token相关)'] = data.token_headers && Object.keys(data.token_headers).length > 0 ? data.token_headers : '(无)'
    result['后端所有请求头'] = data.all_headers
  } catch {
    result['后端诊断'] = '请求失败'
  }

  return result
}

/**
 * 显示带诊断按钮的提示弹窗
 * @param message 提示消息
 */
function showDialogAndClose(message: string): void {
  // 创建遮罩
  const overlay = document.createElement('div')
  Object.assign(overlay.style, {
    position: 'fixed', top: '0', left: '0', width: '100%', height: '100%',
    background: 'rgba(0,0,0,0.5)', zIndex: '99999',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  })

  // 弹窗容器
  const dialog = document.createElement('div')
  Object.assign(dialog.style, {
    background: '#fff', borderRadius: '12px', padding: '20px', width: '85vw', maxWidth: '360px',
    maxHeight: '80vh', overflow: 'auto', fontFamily: 'system-ui, sans-serif',
  })

  // 消息内容
  const msgEl = document.createElement('div')
  Object.assign(msgEl.style, { fontSize: '15px', color: '#333', textAlign: 'center', marginBottom: '16px' })
  msgEl.textContent = message
  dialog.appendChild(msgEl)

  // 诊断结果容器（默认隐藏）
  const diagnoseBox = document.createElement('div')
  Object.assign(diagnoseBox.style, {
    display: 'none', fontSize: '11px', fontFamily: 'monospace', whiteSpace: 'pre-wrap',
    wordBreak: 'break-all', textAlign: 'left', background: '#f5f5f5', borderRadius: '8px',
    padding: '12px', marginBottom: '16px', maxHeight: '50vh', overflow: 'auto',
  })
  dialog.appendChild(diagnoseBox)

  // 按钮区
  const btnRow = document.createElement('div')
  Object.assign(btnRow.style, { display: 'flex', gap: '10px', justifyContent: 'center' })

  // 查看诊断按钮
  const diagBtn = document.createElement('button')
  Object.assign(diagBtn.style, {
    flex: '1', padding: '10px', border: '1px solid #ddd', borderRadius: '8px',
    background: '#f8f9fa', color: '#333', fontSize: '14px', cursor: 'pointer',
  })
  diagBtn.textContent = '查看诊断'
  diagBtn.onclick = async () => {
    diagBtn.textContent = '加载中...'
    diagBtn.disabled = true
    try {
      const info = await collectDiagnoseInfo()
      diagnoseBox.textContent = Object.entries(info).map(([k, v]) =>
        `【${k}】\n${typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}`
      ).join('\n\n')
      diagnoseBox.style.display = 'block'
      diagBtn.style.display = 'none'
    } catch (e) {
      diagnoseBox.textContent = '诊断信息收集失败: ' + String(e)
      diagnoseBox.style.display = 'block'
    }
  }
  btnRow.appendChild(diagBtn)

  // 确定按钮
  const okBtn = document.createElement('button')
  Object.assign(okBtn.style, {
    flex: '1', padding: '10px', border: 'none', borderRadius: '8px',
    background: '#1a73e8', color: '#fff', fontSize: '14px', cursor: 'pointer',
  })
  okBtn.textContent = '确定'
  okBtn.onclick = () => {
    document.body.removeChild(overlay)
    jsBridge.closeWebView()
  }
  btnRow.appendChild(okBtn)

  dialog.appendChild(btnRow)
  overlay.appendChild(dialog)
  document.body.appendChild(overlay)
}

/**
 * 查询后端鉴权开关是否启用
 */
async function isAuthEnabled(): Promise<boolean> {
  try {
    const response = await axios.get<{ enabled: boolean }>('/chat/api/legacy/auth-enabled', { timeout: 5000 })
    return response.data?.enabled ?? true
  } catch {
    // 查询失败时默认启用鉴权（安全优先）
    console.warn('[TokenValidator] 查询鉴权开关失败，默认启用鉴权')
    return true
  }
}

/**
 * 从 localStorage 中查找 token
 * 尝试与 URL 参数相同的三种字段名：appToken、app-token、token
 */
function getTokenFromLocalStorage(): string | null {
  return localStorage.getItem('appToken') || localStorage.getItem('app-token') || localStorage.getItem('token')
}

/**
 * 从 APICloud JSBridge 中提取 token
 * 依次尝试：
 * 1. api.getGlobalData - APP 全局数据（appToken / token / app-token）
 * 2. api.pageParam - 打开页面时传入的参数
 * 3. api.getPrefs - APP 本地偏好存储
 */
function getTokenFromAPICloud(): string | null {
  if (typeof window.api === 'undefined') return null

  const api = window.api as Record<string, unknown>

  // 1. getGlobalData
  if (typeof api.getGlobalData === 'function') {
    for (const key of ['appToken', 'app-token', 'token']) {
      try {
        const val = (api.getGlobalData as (opts: { key: string }) => unknown)({ key })
        if (val && typeof val === 'string' && val.trim()) {
          console.log(`[TokenValidator] 从 api.getGlobalData('${key}') 获取到 token`)
          return val.trim()
        }
      } catch { /* ignore */ }
    }
  }

  // 2. pageParam
  try {
    const pageParam = api.pageParam
    if (pageParam && typeof pageParam === 'object') {
      const pp = pageParam as Record<string, unknown>
      for (const key of ['appToken', 'app-token', 'token']) {
        if (pp[key] && typeof pp[key] === 'string' && (pp[key] as string).trim()) {
          console.log(`[TokenValidator] 从 api.pageParam.${key} 获取到 token`)
          return (pp[key] as string).trim()
        }
      }
    }
  } catch { /* ignore */ }

  // 3. getPrefs
  if (typeof api.getPrefs === 'function') {
    for (const key of ['appToken', 'app-token', 'token', 'app_token']) {
      try {
        const val = (api.getPrefs as (opts: { key: string }) => unknown)({ key })
        if (val && typeof val === 'string' && val.trim()) {
          console.log(`[TokenValidator] 从 api.getPrefs('${key}') 获取到 token`)
          return val.trim()
        }
      } catch { /* ignore */ }
    }
  }

  return null
}

/**
 * 从请求头中提取 token（通过后端接口）
 * APP 的 WebView 可能通过请求头 app-token 传递 token，
 * 前端 JS 无法直接读取页面请求头，需要后端辅助提取。
 */
async function getTokenFromHeader(): Promise<string | null> {
  try {
    const response = await axios.get<{ token: string | null }>(
      '/chat/api/legacy/extract-token',
      { timeout: 5000 }
    )
    return response.data?.token || null
  } catch {
    console.warn('[TokenValidator] 从请求头提取 token 失败')
    return null
  }
}

/**
 * 执行 token 提取与校验流程
 * 在应用启动时调用
 *
 * 无论是否在 APP 环境中，都会尝试提取并存储 token。
 * Token 获取优先级：
 * 1. Cookie（APP_TOKEN）
 * 2. URL 参数（?appToken=xxx 或 ?app-token=xxx 或 ?token=xxx）
 * 3. 请求头（APP WebView 注入的 app-token header，通过后端提取）
 * 4. APICloud JSBridge（getGlobalData / pageParam / getPrefs）
 * 5. localStorage（appToken / app-token / token）
 *
 * 如果没有 token，应用仍可正常运行，但文档搜索等需要 token 的功能将不可用。
 */
export async function checkTokenOnStartup(): Promise<boolean> {
  console.log('[TokenValidator] 开始启动时 token 提取')
  lastTokenSource = null

  // 1. 优先从 Cookie 中获取 token
  let token = getTokenFromCookie()
  if (token) {
    lastTokenSource = 'Cookie (APP_TOKEN)'
  }

  // 2. Cookie 中没有，尝试从 URL 参数提取
  if (!token) {
    console.log('[TokenValidator] Cookie 中未找到 token，尝试从 URL 参数提取')
    token = getTokenFromUrl()
    if (token) lastTokenSource = 'URL参数'
  }

  // 3. URL 中没有，尝试从请求头提取
  if (!token) {
    console.log('[TokenValidator] URL 中未找到 token，尝试从请求头提取')
    token = await getTokenFromHeader()
    if (token) lastTokenSource = '请求头 (Header)'
  }

  // 4. 请求头也没有，尝试从 APICloud JSBridge 提取
  if (!token) {
    console.log('[TokenValidator] 请求头中未找到 token，尝试从 APICloud JSBridge 提取')
    token = getTokenFromAPICloud()
    if (token) lastTokenSource = 'APICloud JSBridge'
  }

  // 5. APICloud 也没有，尝试从 localStorage 提取
  if (!token) {
    console.log('[TokenValidator] APICloud 中未找到 token，尝试从 localStorage 提取')
    token = getTokenFromLocalStorage()
    if (token) {
      lastTokenSource = 'localStorage'
      console.log('[TokenValidator] 从 localStorage 中找到 token')
    }
  }

  // 没有 token，不阻塞应用，只记录日志
  if (!token) {
    lastTokenSource = null
    console.warn('[TokenValidator] 未找到 token，禁止进入页面')
    showDialogAndClose('未登录，请重新进入')
    return false
  }

  console.log('[TokenValidator] 获取到 token:', token.substring(0, 10) + '...', '来源:', lastTokenSource)

  // 5. 查询后端鉴权开关，决定是否校验 token
  const authEnabled = await isAuthEnabled()

  if (!authEnabled) {
    // 鉴权关闭：直接存储，跳过校验
    sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
    console.log('[TokenValidator] 鉴权关闭，已存储 token（跳过校验）')
    return true
  }

  // 6. 鉴权开启：校验 token 有效性
  try {
    const isValid = await validateToken(token)

    if (!isValid) {
      console.warn('[TokenValidator] Token 校验失败')
      showDialogAndClose('登录已失效，请重新登录')
      return false
    }

    console.log('[TokenValidator] Token 校验成功')
    sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
    return true
  } catch (error) {
    console.error('[TokenValidator] Token 校验网络异常:', error)
    showDialogAndClose('网络异常，请稍后重试')
    return false
  }
}
