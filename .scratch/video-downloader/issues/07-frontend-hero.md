# 07 — 前端工程 + 主题 + Hero 解析区

**What to build:** 用户打开首页即看到一个独特精致的深色粉紫渐变主题界面（参考 ai.codefather.cn/painting 风格，营销导向）；在 Hero 区粘贴视频链接即可发起解析，并看到解析结果（标题 / 封面 / 时长 / 清晰度列表）。

**Blocked by:** 01 — 后端骨架 + 解析链路

**Status:** resolved

**验收标准：**
- [x] Vite + Vue 3 独立工程可 `npm run dev` 启动（独立开发，不参考本仓库其他 demo/子项目）
- [x] 设计令牌系统生效（theme.css）：主色 `#eb2f96`、粉紫渐变 `linear-gradient(135deg, #eb2f96, #722ed1)`、深紫黑背景 `#0d0a1a`、16px 圆角
- [x] 导航栏：Logo + 会员入口（渐变描边按钮）
- [x] Hero 区：大标题「全网视频，一键下载」+ 副标题 + 大号链接输入框 + 粉紫渐变解析按钮 + 平台标签云
- [x] 粘贴链接 → 点击解析 → 调用解析接口 → 展示解析结果（标题 / 封面 / 时长 / 清晰度列表）
- [x] 解析中加载态、解析失败错误提示
- [x] 响应式布局（手机浏览器可用）
- [x] 浏览器验收通过（与真实后端联调）

## Comments

- 2026-08-19: T07 完成。实现: `frontend/` Vite 8 + Vue 3.5 独立工程 (DESIGN §3 规划, 未参考仓库其他前端)。theme.css 设计令牌全量落地 (--primary #eb2f96 / --gradient 135deg 粉紫 / --bg-deep #0d0a1a / --card #1a1530 / 16px 圆角 / 数字等宽字体) + 通用类 (btn-gradient / btn-outline-gradient / badge / fade-up 入场动画)。组件: NavBar (Logo「🚀 极速下载」+ 会员渐变描边入口, 会员逻辑 T09) + HeroSection (大标题 + 副标题 + 大号输入框 + 解析按钮 + 旋转加载态 + 平台标签云静态 12 平台) + ResolveResult (封面/标题/平台徽章/时长格式化 h:mm:ss/清晰度下拉, 免费锁定档 disabled + 🔒 标识, 开始下载按钮 T08)。api/client.js fetch 封装统一错误解析 (422/400 detail 透传 + 网络失败提示)。vite.config.js 代理 /api → 127.0.0.1:8000。验证: `npm run build` 通过 (7.4KB CSS / 69KB JS); 所有 SFC 经 dev server 编译 200; 真实联调: 经代理 POST /api/resolve B 站链接 → resolved + 标题/封面/时长 212s/5 档位 (720p 及以下免费, 1080p/best 🔒 锁定正确), 非法链接 422「链接必须以 http:// 或 https:// 开头」, 不支持站点 400 引擎错误透传; 响应式媒体查询 (640px 断点表单堆叠)。后端 8000 + 前端 5173 均已启动, 浏览器验收: http://localhost:5173。提交: `28cb779`。
