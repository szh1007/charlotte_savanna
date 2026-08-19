# 01 — 后端骨架 + 解析链路

**What to build:** 后端服务可启动并健康检查；用户向服务提交一个视频页面链接，服务解析出该视频的元信息（标题、封面、时长、站点）与可用清晰度档位列表；用户可查询服务支持的平台列表。

**Blocked by:** None — can start immediately

**Status:** resolved

**验收标准：**
- [x] 服务可启动，`/api/health` 返回健康状态
- [x] `POST /api/resolve` 合法链接 → 200，返回 task_id / title / cover / duration / site / formats[]（每个档位含 format_id、清晰度、容器格式）
- [x] `POST /api/resolve` 非法或不支持的链接 → 明确错误（4xx + 错误信息）
- [x] 解析任务状态按 pending → resolving → resolved 流转
- [x] `GET /api/sites` 返回非空平台列表（含名称与图标标识）
- [x] 引擎（yt-dlp）调用集中在独立封装模块，解析结果可 mock
- [x] pytest 全部通过（引擎 mock，通过 HTTP 层 TestClient 断言）
- [x] 真实链接脚本级解析验证通过（能拿到真实元信息与档位列表）

## Comments

- 2026-08-19: T01 完成。实现: FastAPI 骨架 (config/schemas/downloader/task_manager/main) + POST /api/resolve 状态机 (pending → resolving → resolved/failed) + GET /api/health + GET /api/sites (引擎能力清单动态校验)。测试: 7 个 pytest 全绿 (TestClient + mock `backend.downloader._extract`)。真实链接验证: YouTube 4K 视频 9 档位 + B 站 1080p 5 档位。提交: `99f66f7`。
