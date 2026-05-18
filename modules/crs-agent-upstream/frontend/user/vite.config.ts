import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:9090'

  return {
    base: '/chat/user/',
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
        '@shared': path.resolve(__dirname, './src/shared'),
      },
    },
    server: {
      port: 5170,
      host: true,
      allowedHosts: true,
      proxy: {
        '/chat/api': {
          target: apiProxyTarget,
          changeOrigin: true,
          secure: false,
          ws: true,
        },
      },
      hmr: {
        overlay: false,
      },
    },
    logLevel: 'info',
    clearScreen: false,
  }
})
