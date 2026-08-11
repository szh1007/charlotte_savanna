import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'request-logger',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          const now = new Date().toISOString()
          console.log(`[${now}] ${req.method} ${req.url}`)
          next()
        })
      },
    },
  ],
})
