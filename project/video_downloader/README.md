# BilibiliDownloader（哔哩哔哩下载器, video_downloader）

> 基于 **FastAPI + yt-dlp + Vue 3** 的哔哩哔哩免费视频下载网站：粘贴链接 → 一键解析 → 选择清晰度 → 批量下载 → 临时直链交付；**AI 视频总结**（转录 / 结构化总结 / 思维导图 / AI 问答, 字幕来源可选 + 模型预下载 + LLM 流式, ADR-0005 ~ 0008）。
> 需求与验收：`.scratch/video-downloader/PRD.md`（总需求）与 `.scratch/video-downloader/issues/`（分步实施记录）。

---

## 一、项目简介

`project/video_downloader` 是一个以学习为目的的哔哩哔哩视频下载网站，实践「方案确认 → 文档先行（CONTEXT/ADR/PRD）→ 分步实现 → 测试验收」的工程模式：

- **核心流程**：粘贴视频链接 → 解析元信息与可用清晰度档位 → 选择档位发起下载 → 任务队列顺序执行 → 生成临时直链 → 手机/电脑随时保存文件。
- **支持范围**：仅支持哔哩哔哩免费公开视频（非会员、非充电内容），URL 域名白名单校验（bilibili.com 主域/子域 + b23.tv 短链），其余平台在引擎调用前拒绝；其他平台预留扩展点（ADR-0004）。
- **下载引擎**：直接嵌入开源项目 **yt-dlp**（Unlicense），零代码改动继承引擎能力；音视频分离流由 **ffmpeg** 合并输出单一 MP4。
- **付费差异（后端强制）**：免费档限 720p / 1 并发 / 队列 5 / 直链 24h / AI 总结每日 3 次 / 问答 10 次；会员（密钥解锁）全部清晰度 / 3 并发 / 队列 50 / 直链 72h / AI 能力无限。
- **无数据库**：任务 / 队列 / 会员会话全部内存态，交付文件 TTL 过期自动清理（ADR-0003）。
- **批量下载**：一次提交多个任务，队列顺序执行 + 并发槽调度（会员任务优先）。

---

## 二、目录结构

```
project/video_downloader/
├── .env                          # 环境变量（不提交，模板见 .env.example）
├── .env.example                  # 环境变量模板（MEMBER_KEY / TTL / 下载目录 / BILI_COOKIE / LLM_* / ASR_* / 模型目录）
├── backend/                      # FastAPI 后端
│   ├── main.py                   # 应用入口（路由注册 + CORS + lifespan 启动调度/清理线程）
│   ├── config.py                 # 从 .env 读取配置（TTL / MEMBER_KEY / 下载目录 / 模型目录）
│   ├── auth.py                   # 会员密钥校验 + 会话 token（内存态, 24h TTL）
│   ├── task_manager.py           # 任务存储 + 状态机 + 并发调度（线程安全, 免费 1 / 会员 3 并发; kind=summary 分流）
│   ├── downloader.py             # yt-dlp 引擎封装（解析 / 下载 / 进度回调 / 平台列表）
│   ├── subtitle.py               # 字幕快路径（BILI_COOKIE 取官方字幕, JSON/VTT/SRT 解析）
│   ├── subtitle_cache.py         # 模型字幕缓存（按 BV 号落盘, TTL 与交付同源, 全局共享命中）
│   ├── model_downloader.py       # 转写模型下载（全局唯一状态机, modelscope, SSE 进度广播）
│   ├── asr.py                    # SenseVoice 转写兜底（funasr + fsmn-vad, 句子级时间戳）
│   ├── llm.py                    # DeepSeek 调用（openai SDK, 流式 Markdown 总结 + SSE 问答, 15 万字符截断）
│   ├── quota.py                  # 免费每日配额（总结 3 / 问答 10, 按匿名 client_id 内存计数）
│   ├── cleaner.py                # TTL 后台清理线程（过期删文件 + 标记 expired + 模型字幕缓存清理）
│   ├── events.py                 # SSE 事件总线（task-update / model-update / 心跳）
│   ├── schemas.py                # Pydantic 请求 / 响应模型
│   └── routers/                  # resolve / downloads / events / member / summarize / model 六组路由
├── frontend/                     # Vue 3 + Vite 前端（独立工程, 零 UI 库）
│   ├── src/
│   │   ├── App.vue / main.js
│   │   ├── views/Home.vue        # 单页布局（Hero + 解析结果 + 任务面板 + 总结弹窗）
│   │   ├── api/client.js         # fetch 封装（自动附加 X-Member-Token）+ EventSource
│   │   ├── composables/useMember.js  # 会员状态 composable（解锁 / 恢复 / 清除）
│   │   ├── styles/theme.css      # 全局主题（设计变量 + 通用组件样式）
│   │   └── components/           # NavBar / HeroSection / ResolveResult / TaskPanel /
│   │                             #   PlatformWall / MemberSection / SiteFooter / SummaryPanel /
│   │                             #   SubtitleSourceBar / MindMapCanvas / ConfirmDialog /
│   │                             #   ErrorAlert / FeaturesSection
│   └── vite.config.js            # dev server + /api 代理到 127.0.0.1:8000
├── scripts/
│   ├── e2e_download.py           # 真实链接 E2E 脚本（解析 → 下载 → 直链取回）
│   ├── check_subtitle_cookie.py  # 验证 BILI_COOKIE 能否取到官方字幕（真实网络, 不 mock）
│   └── probe_ytdlp.py            # yt-dlp cookie 注入诊断探针（对比三种附加方式, 保留用于排查）
├── tests/                        # HTTP seam 自动化测试（210 个, 引擎 / 字幕 / ASR / LLM mock, 无网络依赖）
├── docs/
│   ├── CONTEXT.md                # 领域术语表（Ubiquitous Language）
│   ├── DESIGN.md                 # 设计方案
│   └── adr/                      # ADR-0001 ~ 0008（下载引擎 / 会员密钥 / 内存态 TTL 存储 / 仅 B 站范围收缩 / AI 总结 / 字幕来源与模型下载 / LLM 流式 / Markdown 总结文档）
└── downloads/                    # 交付文件目录（.gitignore, TTL 到期自动清理）
```

---

## 三、环境依赖

| 依赖 | 说明 | 安装 |
|------|------|------|
| Python | 3.13（使用仓库根 `.venv`） | 见仓库根 README |
| ffmpeg | yt-dlp 音视频合并必需（分离流视频必须, 否则无音频） | `winget install ffmpeg`（全局, 需 PATH 生效） |
| yt-dlp | 下载引擎, Python API 嵌入（已装 2026.07.04） | `pip install yt-dlp` |
| funasr + torch | SenseVoice 转写运行时（ADR-0005 兜底链路） | `pip install funasr torch` |
| modelscope | 转写模型下载引擎（ADR-0006, 首次转写 / 手动预下载时使用） | `pip install modelscope` |

> 后端依赖不在根 `requirements.txt`（手动安装, 见上表）；前端为独立工程, 需 `npm install` 一次。

---

## 四、启动说明

### 4.1 后端

```bash
# 在项目根目录（charlotte_savanna）激活虚拟环境后:
cd project/video_downloader
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

- 依赖 MySQL / Redis 之外的任何外部服务（无数据库）。
- `.env` 未配置 `MEMBER_KEY` 时会员功能不可用（一切密钥提交被拒绝）, 请按 `.env.example` 配置。

### 4.2 前端

```bash
cd project/video_downloader/frontend
npm install        # 首次
npm run dev        # 默认 http://localhost:5173, /api 代理到 127.0.0.1:8000
```

生产构建: `npm run build`（产物在 `frontend/dist/`, 部署时由反向代理或后端静态托管做同源）。

### 4.3 .env 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MEMBER_KEY` | （空） | 会员密钥; 前端提交此密钥解锁会员档能力; 空 = 拒绝一切提交 |
| `DOWNLOADS_DIR` | `downloads/` | 交付文件目录 |
| `FREE_DELIVERY_TTL` | `86400`（24h） | 免费用户交付直链有效期（秒） |
| `MEMBER_DELIVERY_TTL` | `259200`（72h） | 会员用户交付直链有效期（秒） |
| `BILI_COOKIE` | （空） | 服务端自备 B 站登录 Cookie, 仅用于取官方字幕（不收集用户 cookie）; 空 = 官方字幕不可用, 前端提示切换模型生成字幕 |
| `MODELS_DIR` | `models/` | 转写模型下载目录（ADR-0006, 持久资产不清理, 约 1GB） |
| `SUBTITLES_DIR` | `models/subtitles/` | 模型字幕缓存目录（转录段 JSON 按 TTL 清理, 与交付 TTL 同源） |
| `LLM_API_KEY` | （空） | DeepSeek API Key（AI 总结必需; 未配置时回退 `DEEPSEEK_API_KEY`） |
| `LLM_BASE_URL` | `https://api.deepseek.com` | LLM 端点 |
| `LLM_MODEL` | `deepseek-chat` | LLM 模型名 |
| `ASR_MODEL` | `iic/SenseVoiceSmall` | SenseVoice 转写主模型（下载至 `MODELS_DIR`） |
| `ASR_VAD_MODEL` | `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | VAD 分段模型（句子级时间戳必需） |
| `ASR_CHUNK_SECONDS` | `600` | 转写分块时长（秒）, 控制峰值内存 |
| `ASR_VAD_MAX_END_SILENCE_MS` | `800` | VAD 句尾静音阈值（ms）: 两句话间隔低于该值合并为一条字幕; 台词间隔短/快速对话调低如 500, 过低如 300 会把正常句子从中切断 |
| `ASR_VAD_SPEECH_TO_SIL_THRES_MS` | （空） | 语音转静音最小时长（ms）, 留空 = 模型默认 |

> 演示 TTL 清理可用缩短配置, 如 `FREE_DELIVERY_TTL=5`。

---

## 五、功能与付费差异

| 能力 | 免费档 | 会员档（密钥解锁） |
|------|--------|----------------|
| 清晰度 | 仅 ≤720p（更高档位标记锁定 🔒） | 全部（1080p / 4K / 最佳画质） |
| 并发下载 | 1 | 3 |
| 批量队列上限 | 5 | 50（超限返回 429） |
| 交付直链有效期 | 24h | 72h |
| AI 视频总结（转录 / 总结 / 思维导图 / 问答） | 每日总结 3 次 + 问答 10 次（按匿名身份, 超限 429） | 无限 |

**后端强制（非 UI 摆设）**：解析结果中 >720p 档位按身份标记锁定；免费用户选择锁定档位被拒（400）；下载执行前重新校验档位访问权（防绕过）；并发 / 队列 / TTL 均按任务创建者身份后端计算。

**会员鉴权**：前端输入密钥 → `POST /api/member` 校验 → 通过签发内存态会话 token（24h）→ 后续请求以 `X-Member-Token` header 携带（前端 localStorage 持久化, 刷新可恢复）。

**AI 视频总结（ADR-0005 ~ 0008）**：解析结果卡点击「AI 总结」→ 创建总结任务（免费档超每日配额 429）→ 后端获取转录文本 → DeepSeek 生成总结 → 完成弹窗展示四个能力：

**字幕来源全局二选一**（ADR-0006, 前端 localStorage 持久化, 默认官方字幕）：
- **官方字幕快路径**：`.env` 配置 `BILI_COOKIE` 且有效时提取官方字幕, 秒级; 未配置时前端经 `has_official_subtitle` 提示
- **模型生成字幕**：SenseVoice 转写（CPU 约 5~15 分钟/小时视频, fsmn-vad 分句产出句子级 `[MM:SS]` 时间戳）；模型缺失时主动选择自动触发预下载, 官方字幕为空回退则提示先下载

**模型预下载 + 字幕缓存**（ADR-0006）：模型（SenseVoiceSmall + fsmn-vad, 约 1GB）统一下载到 `models/`, 全局唯一状态机（missing / downloading / ready）, 进度经 SSE `model-update` 事件广播, 任务取消不中断; 模型生成的字幕按 BV 号落盘缓存（`SUBTITLES_DIR`）, TTL 与交付同源, 全局共享命中不重复扣配额, 官方字幕不缓存。

**LLM 流式输出**（ADR-0007 ~ 0008）：
- 总结 = DeepSeek 流式生成 **Markdown 文档**（章节时间线 + 要点大纲）, 前端 marked 实时渲染打字机, 完成后解析回结构化数据（思维导图同源）
- 问答 = SSE 流式打字机（完整输出才计数配额, 失败/断开不计数）

| 能力 | 说明 |
|------|------|
| 转录全文 | 带句子级时间戳 `[MM:SS]` 的视频文字内容, 可查看 / 复制 / 流式获取 |
| 视频总结 | Markdown 文档（视频概述 / 章节时间线 / 核心要点 / 结论）, 实时渲染 |
| 思维导图 | 由总结结构化数据直接渲染的树形图（markmap, 零 UI 库） |
| AI 问答 | 针对视频内容对话（上下文 = 转录 + 总结, 单次塞入, 不建向量库）, SSE 流式 |

结果随任务 TTL 清理（免费 24h / 会员 72h）, 可导出 **Markdown（总结）/ TXT（转录）** 永久保存; 字幕快路径仅用服务端自备 cookie, 不收集用户任何凭据。

---

## 六、API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/resolve` | 解析链接元信息（标题 / 封面 / 时长 / 站点 / 档位列表, 档位含锁定标记） |
| POST | `/api/downloads` | 创建下载任务（队列超限 429 / 无效档位 400） |
| GET | `/api/tasks` | 任务列表（按创建时间降序, 含 format_id / expires_at） |
| GET | `/api/tasks/{id}` | 单任务详情（不存在 404） |
| DELETE | `/api/tasks/{id}` | 清除任务记录（删文件 + 移除任务; 任意状态可清除, 进行中任务取消下载） |
| POST | `/api/tasks/purge-unfinished` | 一键清除全部未完成记录（排队中/下载中/失败/过期, 含孤儿文件清理） |
| GET | `/api/events` | SSE 进度流（`task-update` 事件 + 约 15s 心跳, 客户端断开自动清理） |
| GET | `/api/files/{id}` | 交付直链下载（未完成 404 / 已过期 410） |
| POST | `/api/member` | 提交会员密钥（正确 200 + token / 错误 401） |
| GET | `/api/member/status` | 当前会话会员状态 |
| POST | `/api/summarize` | 创建 AI 总结任务 `{url, subtitle_source}` → `{task_id}`（subtitle_source: official / model, 缺省 official; 免费超每日配额 429） |
| GET | `/api/tasks/{id}/summary` | 结构化总结（章节时间线 + 要点 JSON, 思维导图数据源） |
| GET | `/api/tasks/{id}/summary/stream` | 总结 SSE 流（ADR-0007 帧协议: `snapshot` / `delta` / `done` / `error` / `heartbeat`; 断线重连不丢文本） |
| GET | `/api/tasks/{id}/transcript` | 带时间戳转录全文 |
| GET | `/api/tasks/{id}/transcript/stream` | 转录全文 SSE 流（长文本渐进加载） |
| POST | `/api/tasks/{id}/qa` | AI 问答 `{question}` → SSE 流式（完整输出才计数配额, 失败/断开不计数; 超限 429 仍为 HTTP 响应） |
| POST | `/api/tasks/{id}/retry` | 重试失败子任务（转录 / 总结, 失败后重新入队） |
| GET | `/api/tasks/{id}/export?format=md\|txt` | 导出总结 Markdown / 转录 TXT（与 TTL 无关） |
| GET | `/api/model/status` | 转写模型状态 `{status, progress, has_official_subtitle}`（missing / downloading / ready, ready 以文件为准） |
| POST | `/api/model/download` | 手动预下载转写模型（幂等: ready 不动作, downloading 返回进度, missing 启动后台下载） |
| GET | `/api/sites` | 支持平台列表（当前仅哔哩哔哩, total 为引擎全量支持数） |
| GET | `/api/health` | 健康检查 |

**SSE 事件协议**：`event: task-update` — `{task_id, status, title, cover, progress, message, url?, error?, expires_at?}`（title/cover 为解析完成的元信息，前端据此补全卡片）；任务清除记录时广播 `{task_id, status: "removed"}`（前端移除卡片）。`event: model-update` — `{status, progress}` 模型下载进度（与任务事件同流, 不受 task_id 过滤）。总结流为独立端点（`/summary/stream`, ADR-0007 命名事件 + 单行 JSON, 0.2s 轮询快照, 不走事件总线防丢帧）。

---

## 七、测试与端到端验证

```bash
# 自动化测试（HTTP seam, 引擎 / 字幕 / ASR / LLM mock, 无真实网络依赖, 约 30s）
python -m pytest -q                       # 210 passed

# 真实链接 E2E（起服务 → 解析 → 选档下载 → 直链取回 → 校验 MP4）
python scripts/e2e_download.py [url] [format_id]
# 默认 B 站公开 MV（仅支持哔哩哔哩域名, 见 ADR-0004）
```

**测试 seam 约定（PRD Testing Decisions）**：只测外部行为（HTTP 请求 → 响应 / SSE 事件），不直接测内部函数；yt-dlp 调用集中在引擎封装层（`backend/downloader.py`），字幕 / ASR / LLM / 模型下载各一层 mock（`test_summarize.py` / `test_model.py` / `test_asr.py` / `test_subtitle_source.py` 等）。流式断言覆盖：帧顺序 / snapshot 断线恢复 / 配额仅成功计数 / 重试清缓冲。

---

## 八、版权与免责声明

- **仅限个人学习使用**：本项目用于学习 yt-dlp 集成、FastAPI 流式与队列调度等工程实践, 请勿用于商业用途。
- **不破解 DRM / 不绕过付费墙**：引擎能力即领域能力边界, 本项目不提供任何绕过能力。
- **封号风险自担**：部分平台对批量下载有限制, 使用下载功能可能面临账号风险, 用户需自行承担。
- **尊重版权**：请仅下载自己拥有版权或已获授权的内容。

---

## 九、相关文档

| 文档 | 位置 |
|------|------|
| 总需求（PRD） | `.scratch/video-downloader/PRD.md` |
| 分步实施 issue（T01 ~ T15） | `.scratch/video-downloader/issues/` |
| 领域术语表 | `project/video_downloader/docs/CONTEXT.md` |
| 设计方案 | `project/video_downloader/docs/DESIGN.md` |
| 架构决策记录 | `project/video_downloader/docs/adr/`（ADR-0001 下载引擎 / 0002 会员密钥 / 0003 内存态 TTL 存储 / 0004 仅 B 站范围收缩 / 0005 AI 视频总结 / 0006 字幕来源与模型下载 / 0007 LLM 流式输出 / 0008 总结 Markdown 文档） |
