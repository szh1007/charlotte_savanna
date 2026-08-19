# DESIGN.md — 万能视频下载站设计方案

> 状态：待用户终审。确认后按「分步实施计划」开发。
> 领域术语参见 [CONTEXT.md](./CONTEXT.md)，架构决策参见 [docs/adr/](./docs/adr/)。

---

## 1. 产品定位

**万能视频下载站**：粘贴链接 → 解析 → 选清晰度 → 下载 → 手机/电脑随时保存。
核心价值：一个站点覆盖 2000+ 平台（B 站 / 抖音 / YouTube / 小红书 / 微博 / 快手…），批量下载、清晰度自选。

UI 基调：**吸引付费**。参考 `ai.codefather.cn/painting` 的卡片网格 + 粉色渐变风格，但做深色 Hero + 霓虹粉紫渐变，突出「会员解锁」的营销感。

## 2. 架构总览

```
┌─────────────┐  HTTP/JSON + SSE   ┌───────────────────────┐
│  前端        │ ──────────────────►│  后端 FastAPI          │
│  Vue 3+Vite │                    │  ├─ 解析 API (yt-dlp   │
│  (独立工程)  │ ◄──────────────────│  │   extract_info)     │
└─────────────┘   进度流 / 直链    │  ├─ 任务管理(内存态)   │
                                   │  ├─ 下载引擎 yt_dlp    │
                                   │  ├─ SSE 进度推送       │
                                   │  └─ TTL 清理任务       │
                                   └──────────┬────────────┘
                                              │ yt_dlp Python API
                                   ┌──────────▼────────────┐
                                   │ yt-dlp 引擎 (Unlicense)│
                                   │  + ffmpeg (合并音视频) │
                                   └───────────────────────┘
```

- 后端：FastAPI + uvicorn，**无数据库**（内存态任务，见 ADR-0003）
- 下载引擎：`yt_dlp` Python API 嵌入，`progress_hooks` 上报进度（见 ADR-0001）
- 交付：文件落 `downloads/`，直链 GET 下载，TTL 清理
- 前端：Vue 3 + Vite，CSS 变量主题系统，手写样式（零 UI 库，保证独特）

## 3. 目录结构

```
project/video_downloader/
├── backend/
│   ├── main.py            # FastAPI 入口 + 路由注册 + CORS
│   ├── config.py          # .env 读取（MEMBER_KEY / 路径 / TTL）
│   ├── schemas.py         # Pydantic 模型（任务/解析结果/会员）
│   ├── downloader.py      # yt_dlp 封装：解析 + 下载 + 进度回调
│   ├── task_manager.py    # 内存态任务存储 + 队列调度 + 并发控制
│   ├── cleaner.py         # TTL 后台清理（文件删除 + 状态过期）
│   ├── auth.py            # 会员密钥校验依赖
│   └── routers/
│       ├── resolve.py     # POST /api/resolve
│       ├── downloads.py   # POST /api/downloads, GET /api/tasks, GET /api/files
│       ├── events.py      # GET /api/events (SSE)
│       └── member.py      # POST /api/member, GET /api/member/status
├── frontend/              # Vue 3 + Vite 独立工程
│   └── src/
│       ├── styles/theme.css    # 设计令牌（色板/圆角/阴影/字体）
│       ├── api/client.js       # fetch 封装 + SSE 客户端
│       ├── components/         # Hero/解析面板/任务面板/平台墙/会员区…
│       └── views/Home.vue      # 单页布局（Hero→解析→任务→平台→会员→页脚）
├── downloads/             # 交付文件（.gitignore）
├── CONTEXT.md
├── docs/adr/
├── DESIGN.md              # 本文件
└── README.md              # 启动说明
```

## 4. API 设计

| 方法 | 路径 | 说明 | 请求 → 响应 |
|------|------|------|-------------|
| POST | `/api/resolve` | 解析链接元信息 | `{url}` → `{task_id, title, cover, duration, site, formats[], member_limited}` |
| POST | `/api/downloads` | 创建下载任务 | `{url, format_id}` → `{task_id}` |
| GET | `/api/tasks` | 任务列表（降序） | → `{tasks: Task[]}` |
| GET | `/api/tasks/{id}` | 单任务详情 | → `Task` |
| GET | `/api/events` | SSE 进度流 | `?task_ids=1,2` → 事件流 |
| GET | `/api/files/{id}` | 交付直链下载 | → 文件流（404 若过期） |
| POST | `/api/member` | 提交会员密钥 | `{key}` → `{is_member, expires_at}` |
| GET | `/api/member/status` | 会话会员状态 | → `{is_member}` |
| GET | `/api/sites` | 支持平台列表 | → `{sites: [{name, icon}]}` |

**SSE 事件**（`event: task-update`）：`{task_id, status, progress, message, url?, error?}`

## 5. 付费差异（后端强制）

| 能力 | 免费档 | 会员档（密钥） |
|------|--------|----------------|
| 清晰度 | 仅 ≤720p | 全部（1080p/4K/最佳画质） |
| 并发下载数 | 1 | 3 |
| 批量队列上限 | 5 | 50 |
| 交付直链有效期 | 24h | 72h |
| 解析/下载频率 | 宽松限速 | 不限 |

实现：任务创建时校验 `member` 标记，格式列表按档位过滤，队列调度按档位分配并发槽。

## 6. UI 设计规范（参考 painting 风格）

**设计令牌**（`theme.css`）：

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--primary` | `#eb2f96` 品红粉 | 主色（与参考站一致） |
| `--gradient` | `linear-gradient(135deg, #eb2f96, #722ed1)` | 按钮/Hero 高光 |
| `--bg-deep` | `#0d0a1a` 深紫黑 | Hero / 全局背景 |
| `--card` | `#ffffff` 或 `#1a1530` | 卡片 |
| 圆角 | `16px` | 卡片 / 输入框 |
| 字体 | 系统栈 + 数字等宽 | 进度/大小展示 |

**页面结构**（单页 Home，从上到下）：

1. **导航栏**：Logo「🚀 极速下载」+ 会员入口（渐变描边按钮）
2. **Hero 区**（深色渐变 + 光斑）：大标题「全网视频，一键下载」+ 副标题「支持 2000+ 平台 · 批量下载 · 高清任选」+ **大号链接输入框** + 解析按钮（粉紫渐变）+ 平台标签云（B站/抖音/YouTube/小红书…）
3. **解析结果卡**（解析成功后浮现，带入场动画）：封面图 + 标题 + 平台徽章 + 时长 + 清晰度下拉（免费档选项后带 🔒 锁标）→「开始下载」
4. **下载任务面板**：任务卡片列表（封面缩略图 + 进度条 + 状态徽章 + 完成后的「下载到手机/电脑」按钮 + 复制链接）
5. **平台墙**：卡片网格（每平台一张卡：icon + 名称 + 支持格式），粉彩描边 hover 上浮
6. **会员营销区**（深色对比块）：功能对比表 + 「限时」倒计时 + 密钥输入框 + 解锁提示动画
7. **页脚**：版权免责声明（仅个人学习使用、不破解 DRM、封号风险自担、尊重版权）

**付费引导文案**（参考站风格，强实用价值导向）：
- 免费档：「免费下载 · 最高 720p · 适合快速预览」
- 会员档：「解锁 4K 高清 · 批量 50 个 · 3 倍速并发 · 72h 文件保留」

## 7. 分步实施计划

| 步骤 | 内容 | 验证项 |
|------|------|--------|
| 1 | 环境：winget 装 ffmpeg；.venv 装 yt-dlp/fastapi/uvicorn | `ffmpeg -version`、`yt_dlp --version` |
| 2 | 后端：config + downloader（解析/下载/进度回调）+ task_manager + cleaner + routers | 脚本级自测：解析真实链接、下载到文件 |
| 3 | 后端测试：pytest 单测（任务状态机、档位过滤、并发槽、TTL 逻辑 mock） | `pytest` 全绿 |
| 4 | 后端联调：uvicorn 起服务，curl 走完 解析→下载→SSE→直链 | curl 验证全链路 |
| 5 | 前端工程：Vite 脚手架 + theme.css 设计令牌 | `npm run dev` 渲染主题 |
| 6 | 前端页面：导航 → Hero → 解析面板 → 任务面板 → 平台墙 → 会员区 → 页脚 | 浏览器逐块验收 |
| 7 | 前后端联调：真实下载全流程（含会员解锁 1080p） | 端到端演示 |
| 8 | 文档收尾：README（启动说明）+ CLAUDE.md 项目结构同步 | — |
| 9 | 找你验收 | 演示 + 交付清单 |

## 8. 明确不做（Phase 2 计划）

- 视频总结 / 字幕翻译（LLM 能力）— 已列入后续需求计划，本次不开发
- 真实支付 / 账号体系 — 密钥模拟（ADR-0002）
- 多实例部署 / 任务持久化 — 内存态（ADR-0003）
- DRM 破解 / 会员视频绕过 — 领域红线，永不涉及
