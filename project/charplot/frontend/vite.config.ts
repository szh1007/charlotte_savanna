import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    port: 9004,
    proxy: {
      // Django 业务侧 (app/charplot, 主 Django 8000)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 公开分享页 (Issue 06): /r/{slug} 服务端渲染, 代理到 Django
      '/r': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // FastAPI AI 能力侧
      '/ai': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
      },
    },
  },
})
