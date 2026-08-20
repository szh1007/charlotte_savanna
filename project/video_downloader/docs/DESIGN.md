# DESIGN.md — BilibiliDownloader 设计方案

> 状态：待用户终审。确认后按「分步实施计划」开发。
> 领域术语参见 [CONTEXT.md](./CONTEXT.md)，架构决策参见 [docs/adr/](./docs/adr/)。
> 修订（2026-08-20）：产品范围已收缩为仅支持哔哩哔哩免费公开视频，见 [ADR-0004](./adr/0004-only-bilibili-free-videos.md)。

---

## 1. 产品定位

**BilibiliDownloader**：粘贴哔哩哔哩链接 → 解析 → 选清晰度 → 下载 → 手机/电脑随时保存。
核心价值：专注哔哩哔哩免费公开视频（非会员、非充电内容），域名白名单校验（bilibili.com / b23.tv），批量下载、清晰度自选；其他平台预留扩展点（ADR-0004）。

UI 基调：**吸引付费**。浅色 B 站品牌粉蓝主题（主蓝 `#00AEEC` + 主粉 `#FB7299`），浅粉→浅蓝渐变背景 + 白卡，突出「会员解锁」的营销感。

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
│   ├── cleaner.py         # TTL 后台清理 + 手动清除记录（文件删除 + 任务移除 + 孤儿文件）
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
│       ├── components/         # Hero/解析面板/任务面板/会员弹窗…
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
| DELETE | `/api/tasks/{id}` | 清除任务记录（删文件+移除任务; 进行中任务取消下载） | → 204（不存在 404 / 文件占用 409） |
| POST | `/api/tasks/purge-unfinished` | 一键清除全部未完成记录（排队中/下载中/失败/过期, 含孤儿文件） | → `{removed: int[]}` |
| GET | `/api/events` | SSE 进度流 | `?task_ids=1,2` → 事件流 |
| GET | `/api/files/{id}` | 交付直链下载 | → 文件流（404 若过期 / 410 已过期清理） |
| POST | `/api/member` | 提交会员密钥 | `{key}` → `{is_member, expires_at}` |
| GET | `/api/member/status` | 会话会员状态 | → `{is_member}` |
| GET | `/api/sites` | 支持平台列表（当前仅哔哩哔哩, 见 ADR-0004） | → `{sites: [{name, icon}], total}` |

**SSE 事件**（`event: task-update`）：`{task_id, status, title, cover, progress, message, url?, error?, expires_at?}`（title/cover 为解析完成的元信息，前端据此补全卡片）；任务清除记录时广播 `{task_id, status: "removed"}`（非状态机状态，前端据此移除卡片）

**任务对象**（`Task`）：`{task_id, kind, status, title, cover, duration, site, formats[], format_id, progress, message, error, expires_at?, created_at}`——`format_id` 为选定档位（标题旁清晰度标注），`expires_at` 仅 completed 携带（交付过期时刻 = 完成时刻 + 身份 TTL，前端倒计时）

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
| `--primary` | `#fb7299` B 站粉 | 主色（B 站品牌色） |
| `--blue` | `#00aeec` B 站蓝 | 渐变终点 / 聚焦描边 |
| `--gradient` | `linear-gradient(135deg, #fb7299, #00aeec)` | 按钮/Hero 高光 |
| `--bg-deep` | `#fff5f8` 浅粉 → `#ebf8ff` 浅蓝 | 全局渐变背景 |
| `--card` | `#ffffff` | 白卡 |
| 圆角 | `16px` | 卡片 / 输入框 |
| 字体 | 系统栈 + 数字等宽 | 进度/大小展示 |

**页面结构**（单页 Home，从上到下）：

1. **导航栏**：Logo「🅱️ BilibiliDownloader」+ 会员入口（渐变描边按钮，点击打开会员弹窗）
2. **Hero 区**（浅色渐变 + 光斑）：大标题「哔哩哔哩视频，一键下载」+ 副标题「免费视频 · 高清任选 · 批量下载」+ **大号链接输入框**（占位文案「粘贴视频链接」）+ 解析按钮（粉蓝渐变）
3. **解析结果卡**（解析成功后浮现，带入场动画）：封面图 + 标题 + 平台徽章 + 时长 + 清晰度下拉（免费档选项后带 🔒 锁标）→「开始下载」
4. **下载任务面板**：任务卡片列表（封面缩略图 + 进度条 + 状态徽章 + 完成后的「下载到手机/电脑」按钮 + 复制链接）
5. **会员弹窗**（导航栏/结果卡解锁引导触发，Teleport 到 body）：功能对比表 + 「限时」倒计时 + 密钥输入框（未解锁）+ 权益状态与有效期（已解锁）
6. **页脚**：版权免责声明（仅个人学习使用、不破解 DRM、封号风险自担、尊重版权）

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

- 字幕翻译（LLM 能力）— 已从需求中移除（原 Phase 2 计划项, 当前不规划）
- 真实支付 / 账号体系 — 密钥模拟（ADR-0002）
- 多实例部署 / 任务持久化 — 内存态（ADR-0003）
- DRM 破解 / 会员视频绕过 — 领域红线，永不涉及

## 9. AI 视频总结能力（Phase 2, ADR-0005）

> 需求与验收：`.scratch/video-downloader/issues/12-ai-video-summary.md`；架构决策：ADR-0005。

### 9.1 能力清单

| 能力 | 说明 |
|------|------|
| 视频总结 | LLM 生成 Markdown 总结文档，后端解析回结构化数据（章节时间线 + 要点, ADR-0008） |
| 转录全文 | 带时间戳的视频文字内容（Transcript）, 可查看 / 复制 / 导出 |
| 思维导图 | 从总结结构化数据渲染的树形图（前端手写 CSS, 零 UI 库） |
| AI 问答 | 针对视频内容对话（上下文 = 转录 + 总结, 单次塞入, 不建向量库） |

### 9.2 内容获取管线（字幕优先 + ASR 回退）

```
POST /api/summarize {url}
  → 总结任务 (kind=summary, 并入任务体系)
  → transcribing:
      字幕快路径 (秒级): BILI_COOKIE 配置且有效 → 提取官方字幕
        未配置 / 无字幕 / 失败 ──► 兜底: 下载音频流 → SenseVoice 转写
        (1h 视频 CPU 约 5~15 分钟, SSE 进度可见)
  → summarizing: DeepSeek (openai SDK) 流式生成 Markdown 总结文档 (ADR-0008)
     流式期间前端 marked 实时渲染, 流结束 parse_summary_text 解析回结构化 dict
  → completed: {transcript, summary_dict, mindmap 数据} 随任务保存
```

- **不收集用户凭据**：`BILI_COOKIE` 为服务端自备（.env, 不提交）, 留空则跳过字幕直走 ASR
- **付费差异（后端强制）**：免费每日配额（总结 3 / 问答 10, 按匿名 client_id + 日窗口计数, 内存态重启清零）, 会员无限
- **结果保留**：转录与总结随任务 TTL 清理（复用 cleaner）; 导出 Markdown / TXT 供用户永久保存

### 9.3 新增 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/summarize` | 创建总结任务 `{url}` → `{task_id}`; 免费超每日配额 429 |
| GET | `/api/tasks/{id}/summary` | 结构化总结（Markdown 解析回章节时间线 + 要点, ADR-0008） |
| GET | `/api/tasks/{id}/summary/stream` | 总结生成过程流式输出（SSE, ADR-0007/0008）: Markdown 文档增量, `snapshot` 首帧累积全文 → `delta` 增量 → `done`/`error`, 空闲 15s `heartbeat` |
| GET | `/api/tasks/{id}/transcript` | 带时间戳转录文本 |
| POST | `/api/tasks/{id}/qa` | AI 问答流式回答（SSE, ADR-0007）: `{question}` → `delta` 增量 → `done`/`error` 收尾; 免费超每日配额 429（流开始前以 HTTP 状态返回） |
| GET | `/api/tasks/{id}/export` | 导出总结 / 转录为 Markdown / TXT |

### 9.4 状态机扩展

```
总结任务 (kind=summary):
pending → queued → transcribing → summarizing → completed
                ↘                ↘
                 failed            failed
completed → expired（转录与总结随 TTL 清理; 用户可导出永久保存）
```

状态与下载任务复用同一调度器：`pending` 内同步做轻量元信息解析（不落档位,
不经过 `resolving`, 失败不阻塞总结）, 入队后由调度线程派发——按 kind 分流为
`transcribing`（总结）或 `downloading`（下载）。

SSE 事件协议不变（`task-update`）, 新增 `transcribing` / `summarizing` 状态,
进度消息含 ASR 百分比; 前端任务面板按 kind 区分下载卡片与总结卡片。

### 9.5 新增依赖

| 依赖 | 用途 | 说明 |
|------|------|------|
| `openai` | DeepSeek LLM 调用（兼容 SDK） | 轻量 |
| `funasr` | SenseVoice 转写 | 依赖已装的 torch / modelscope; 模型下载约 1GB |
| `yt-dlp`（已有） | 字幕提取 / 音频流下载 | 引擎层扩展 |

## 10. 字幕来源与模型下载（Phase 3, ADR-0006）

> 需求与验收：`.scratch/video-downloader/issues/13-subtitle-source-model-download.md`；架构决策：ADR-0006。

### 10.1 能力清单

| 能力 | 说明 |
|------|------|
| 字幕来源全局设置 | 官方字幕 / 模型生成字幕二选一（localStorage 持久化, 默认官方字幕）, 创建总结任务时作为快照传参 |
| 模型预下载 | 语音转写模型（约 1GB）可提前下载, 状态机 missing / downloading / ready, 幂等触发 |
| 模型字幕缓存 | 模型生成的字幕按 BV 号缓存（分 P 加 `_pN` 后缀）, 全局共享命中, 命中不另扣配额 |
| 自动下载联动 | 选模型生成且模型缺失时转录子任务自动触发下载（进度可见, 任务取消不中断） |

### 10.2 转录双路径

```
POST /api/summarize {url, subtitle_source: official|model}
  → 总结任务 → transcribing:
      官方字幕 (subtitle_source=official, 默认):
        提取官方字幕 (秒级, 不写缓存)
        空 ──► 回退模型生成: 仅校验模型存在, 缺失 → 转录 failed
              提示「请先下载模型」(不自动触发 1GB 下载)
      模型生成 (subtitle_source=model):
        查字幕缓存 (全局共享, 命中退还配额) → 未命中:
          模型缺失 ──► 自动触发下载 (转录进度 0~50 显示「模型下载中 x%」)
          → 转写 (进度 50~55 音频 / 55~100 转写) → 写缓存
```

- 进度映射（ADR-0006）: 模型下载 0~50 → 音频下载 50~55 → 转写 55~100
- 缓存键 = BV 号（创建时轻量解析, 失败跳过缓存不阻塞转录）; TTL 按创建者身份: 免费 24h / 会员 72h, 与交付 TTL 同源
- 模型是全局持久资产（`models/` 目录, .gitignore）: 不随 TTL 清理, 任务取消不中断下载

### 10.3 新增 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/model/status` | 模型状态 `{status: missing\|downloading\|ready, progress, has_official_subtitle}`（ready = config.yaml + model.pt 存在; has_official_subtitle = 服务端是否配置 BILI_COOKIE） |
| POST | `/api/model/download` | 下载模型（幂等）: 缺失启动后台线程, 下载中返回当前进度, 已就绪不动作 |

SSE 新增 `model-update` 事件: `{status, progress}`, 与任务事件同流广播（`publish_all`, 不受 task_id 过滤）; 前端据此更新模型状态区（下载进度 / 完成 / 失败回 missing）。

### 10.4 前端

- 全局设置条（解析区与任务面板之间）: 字幕来源 radio + 模型状态区（缺失 → 下载按钮; 下载中 → 进度条; 就绪 → 绿标）+ 未配置 Cookie 提示（选官方字幕且 `has_official_subtitle=false` 时提示将自动回退）
- 字幕来源选择 localStorage 持久化（`vd_subtitle_source`）, 创建任务时随请求携带
