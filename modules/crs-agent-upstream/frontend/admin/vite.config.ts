import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:9090'

  return {
    base: '/chat/admin/',
    plugins: [react()],
    server: {
      port: 5171,
      host: true,
      allowedHosts: true,
      proxy: {
        '/chat/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        }
      }
    }
  }
})
