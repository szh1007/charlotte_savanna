# 万能视频下载站 — 前端工程

Vue 3 + Vite 独立工程（手写样式, 零 UI 库）, 营销导向的深色粉紫渐变主题。

## 启动

```bash
npm install     # 首次
npm run dev     # 开发服务器 (默认 5173 端口)
```

## 与后端联调

- 后端要求已启动: `uvicorn backend.main:app --port 8000`（详见项目根 README）
- 开发期前端通过 Vite 代理访问 `/api/*` → `http://127.0.0.1:8000`（见 `vite.config.js`）, 无需 CORS 配置
- 生产部署由反向代理处理后端同源

## 目录结构

```
src/
├── main.js              # 入口 (引入主题)
├── App.vue              # 根组件
├── styles/theme.css     # 设计令牌 (色板/渐变/圆角/字体) + 全局样式
├── api/client.js        # fetch 封装 (resolve / sites / member)
├── views/Home.vue       # 单页布局
└── components/          # NavBar / HeroSection / ResolveResult (按 ticket 逐步扩展)
```
