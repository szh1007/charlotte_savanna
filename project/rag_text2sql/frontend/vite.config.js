import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
    plugins: [vue()],
    server: {
        port: 8201,
        proxy: {
            "/api": {
                target: "http://localhost:8200",
                changeOrigin: true,
                configure: (proxy) => {
                    proxy.on("proxyReq", (proxyReq) => {
                        proxyReq.setHeader("Cache-Control", "no-cache");
                        proxyReq.setHeader("Connection", "keep-alive");
                    });
                },
            },
        },
    },
});
