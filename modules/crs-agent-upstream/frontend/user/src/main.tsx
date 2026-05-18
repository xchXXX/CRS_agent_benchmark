import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles/index.css'
import { checkTokenOnStartup } from './utils/tokenValidator'

// 启动应用
async function startApp() {
  // 执行 token 校验
  const isTokenValid = await checkTokenOnStartup()

  // 如果 token 无效，不渲染应用（页面会被关闭）
  if (!isTokenValid) {
    console.log('[App] Token 校验失败，应用不会启动')
    return
  }

  // Token 有效或不在 APP 环境中，正常启动应用
  console.log('[App] Token 校验通过，启动应用')
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}

// 启动应用
startApp()
