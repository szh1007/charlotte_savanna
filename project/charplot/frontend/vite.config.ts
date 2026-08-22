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
    port: 4300,
    proxy: {
      // Django 业务侧 (app/charplot)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // FastAPI AI 能力侧
      '/ai': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
