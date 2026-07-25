# 34 — Vue 3 — 项目 scaffold & 基础布局

**What to build:** Vue 3 + Vite + Naive UI 项目创建、路由、Pinia、导航栏、登录态管理。

**Blocked by:** 05 — JWT 认证

**Status:** ready-for-agent

- [ ] `npm create vite@latest miniblog-frontend -- --template vue-ts` 在 `/miniblog/` 同级的 `/miniblog-frontend/` 目录创建项目
- [ ] 安装依赖：`vue-router`, `pinia`, `naive-ui`, `@vicons/ionicons5`, `marked`(Markdown 渲染), `axios`
- [ ] 配置 Tailwind CSS（可选，Naive UI 主用）
- [ ] 路由配置：`/login`, `/register`, `/reset-password`, `/`, `/post/:id`, `/editor`, `/editor/:id`, `/user/:id`, `/profile`, `/category/:slug`, `/search`, `/assistant`, `/admin` 及其子路由
- [ ] Pinia store：`useAuthStore`（login/logout/refresh/user state/token 管理，axios interceptor 自动刷新 token）
- [ ] 导航栏组件：Logo + 首页/分区/搜索入口 + AI 助手入口 + 通知铃铛（unread badge） + 头像下拉菜单（个人中心/我的空间/写帖子/管理后台入口（仅 admin）/ 退出）
- [ ] 401 响应自动跳转登录页
