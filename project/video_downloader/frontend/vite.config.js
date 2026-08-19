import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期通过 Vite 代理访问后端 (uvicorn 默认 8000 端口), 前端代码统一走相对路径 /api
// 生产部署时由反向代理 (Nginx 等) 或后端静态托管处理同源
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
