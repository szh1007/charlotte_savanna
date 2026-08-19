# 万能视频下载站（video_downloader）

> 基于 **FastAPI + yt-dlp + Vue 3** 的视频下载网站：粘贴链接 → 一键解析 → 选择清晰度 → 批量下载 → 临时直链交付。
> 需求与验收：`.scratch/video-downloader/PRD.md`（总需求）与 `.scratch/video-downloader/issues/`（分步实施记录）。

---

## 一、项目简介

`project/video_downloader` 是一个以学习为目的的视频下载网站，实践「方案确认 → 文档先行（CONTEXT/ADR/PRD）→ 分步实现 → 测试验收」的工程模式：

- **核心流程**：粘贴视频链接 → 解析元信息与可用清晰度档位 → 选择档位发起下载 → 任务队列顺序执行 → 生成临时直链 → 手机/电脑随时保存文件。
- **下载引擎**：直接嵌入开源项目 **yt-dlp**（Unlicense，支持约 2000 个平台），零代码改动继承引擎能力；音视频分离流由 **ffmpeg** 合并输出单一 MP4。
- **付费差异（后端强制）**：免费档限 720p / 1 并发 / 队列 5 / 直链 24h；会员（密钥解锁）全部清晰度 / 3 并发 / 队列 50 / 直链 72h。
- **无数据库**：任务 / 队列 / 会员会话全部内存态，交付文件 TTL 过期自动清理（ADR-0003）。
- **批量下载**：一次提交多个任务，队列顺序执行 + 并发槽调度（会员任务优先）。

---

## 二、目录结构

```
project/video_downloader/
├── .env                          # 环境变量（不提交，模板见 .env.example）
├── .env.example                  # 环境变量模板（MEMBER_KEY / TTL / 下载目录）
├── backend/                      # FastAPI 后端
│   ├── main.py                   # 应用入口（路由注册 + CORS + lifespan 启动调度/清理线程）
│   ├── config.py                 # 从 .env 读取配置（TTL / MEMBER_KEY / 下载目录）
│   ├── auth.py                   # 会员密钥校验 + 会话 token（内存态, 24h TTL）
│   ├── task_manager.py           # 任务存储 + 状态机 + 并发调度（线程安全, 免费 1 / 会员 3 并发）
│   ├── downloader.py             # yt-dlp 引擎封装（解析 / 下载 / 进度回调 / 平台列表）
│   ├── cleaner.py                # TTL 后台清理线程（过期删文件 + 标记 expired）
│   ├── events.py                 # SSE 事件总线（task-update / 心跳）
│   ├── schemas.py                # Pydantic 请求 / 响应模型
│   └── routers/                  # resolve / downloads / events / member 四组路由
├── frontend/                     # Vue 3 + Vite 前端（独立工程, 零 UI 库）
│   ├── src/
│   │   ├── App.vue / main.js
│   │   ├── api/client.js         # fetch 封装（自动附加 X-Member-Token）+ EventSource
│   │   ├── composables/useMember.js  # 会员状态 composable（解锁 / 恢复 / 清除）
│   │   └── components/           # NavBar / HeroSection / ResolveResult / TaskPanel /
│   │                             #   PlatformWall / MemberSection / SiteFooter
│   └── vite.config.js            # dev server + /api 代理到 127.0.0.1:8000
├── scripts/e2e_download.py       # 真实链接 E2E 脚本（解析 → 下载 → 直链取回）
├── tests/                        # HTTP seam 自动化测试（54 个, 引擎 mock, 无网络依赖）
├── docs/
│   ├── CONTEXT.md                # 领域术语表（Ubiquitous Language）
│   ├── DESIGN.md                 # 设计方案
│   └── adr/                      # ADR-0001 ~ 0003（下载引擎 / 会员密钥 / 内存态 TTL 存储）
└── downloads/                    # 交付文件目录（.gitignore, TTL 到期自动清理）
```

---

## 三、环境依赖

| 依赖 | 说明 | 安装 |
|------|------|------|
| Python | 3.13（使用仓库根 `.venv`） | 见仓库根 README |
| ffmpeg | yt-dlp 音视频合并必需（分离流视频必须, 否则无音频） | `winget install ffmpeg`（全局, 需 PATH 生效） |
| yt-dlp | 下载引擎, Python API 嵌入（已装 2026.07.04） | `pip install yt-dlp` |

> 前端为独立工程, 需 `npm install` 一次。

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

> 演示 TTL 清理可用缩短配置, 如 `FREE_DELIVERY_TTL=5`。

---

## 五、功能与付费差异

| 能力 | 免费档 | 会员档（密钥解锁） |
|------|--------|----------------|
| 清晰度 | 仅 ≤720p（更高档位标记锁定 🔒） | 全部（1080p / 4K / 最佳画质） |
| 并发下载 | 1 | 3 |
| 批量队列上限 | 5 | 50（超限返回 429） |
| 交付直链有效期 | 24h | 72h |

**后端强制（非 UI 摆设）**：解析结果中 >720p 档位按身份标记锁定；免费用户选择锁定档位被拒（400）；下载执行前重新校验档位访问权（防绕过）；并发 / 队列 / TTL 均按任务创建者身份后端计算。

**会员鉴权**：前端输入密钥 → `POST /api/member` 校验 → 通过签发内存态会话 token（24h）→ 后续请求以 `X-Member-Token` header 携带（前端 localStorage 持久化, 刷新可恢复）。

---

## 六、API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/resolve` | 解析链接元信息（标题 / 封面 / 时长 / 站点 / 档位列表, 档位含锁定标记） |
| POST | `/api/downloads` | 创建下载任务（队列超限 429 / 无效档位 400） |
| GET | `/api/tasks` | 任务列表（按创建时间降序） |
| GET | `/api/tasks/{id}` | 单任务详情（不存在 404） |
| GET | `/api/events` | SSE 进度流（`task-update` 事件 + 约 15s 心跳, 客户端断开自动清理） |
| GET | `/api/files/{id}` | 交付直链下载（未完成 404 / 已过期 410） |
| POST | `/api/member` | 提交会员密钥（正确 200 + token / 错误 401） |
| GET | `/api/member/status` | 当前会话会员状态 |
| GET | `/api/sites` | 支持平台列表（含每平台支持格式） |
| GET | `/api/health` | 健康检查 |

**SSE 事件协议**（`event: task-update`）：`{task_id, status, progress, message, url?, error?}`

---

## 七、测试与端到端验证

```bash
# 自动化测试（HTTP seam, 引擎 mock, 无真实网络依赖, 约 15s）
python -m pytest -q                       # 54 passed

# 真实链接 E2E（起服务 → 解析 → 选档下载 → 直链取回 → 校验 MP4）
python scripts/e2e_download.py [url] [format_id]
# 默认 B 站公开 MV（YouTube 需 cookies 验证, 不适合无头 E2E）
```

**测试 seam 约定（PRD Testing Decisions）**：只测外部行为（HTTP 请求 → 响应 / SSE 事件），不直接测内部函数；yt-dlp 调用集中在引擎封装层（`backend/downloader.py`），测试中 mock 该层。

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
| 分步实施 issue（T01 ~ T10） | `.scratch/video-downloader/issues/` |
| 领域术语表 | `project/video_downloader/docs/CONTEXT.md` |
| 设计方案 | `project/video_downloader/docs/DESIGN.md` |
| 架构决策记录 | `project/video_downloader/docs/adr/`（ADR-0001 下载引擎 / 0002 会员密钥 / 0003 内存态 TTL 存储） |
