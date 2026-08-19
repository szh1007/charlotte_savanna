# 06 — TTL 清理 + 直链过期

**What to build:** 交付文件不是永久资产：后台任务周期性扫描，将超过有效期的已完成任务标记为过期并删除其文件；过期任务的直链返回明确过期响应；免费档有效期 24h，会员档 72h。

**Blocked by:** 02 — 下载 + 交付直链链路, 04 — 会员鉴权

**Status:** resolved

**验收标准：**
- [x] 后台清理周期性执行（约 60s 周期）
- [x] completed 任务超过 TTL → 文件被删除 + 任务标记 expired
- [x] TTL 按身份区分：免费任务 24h / 会员任务 72h
- [x] expired 任务的直链请求返回 404/410 与明确提示（文件已清理）
- [x] 清理逻辑不误伤未过期任务，重复清理幂等
- [x] 时间判定使用可注入时钟（测试可推进时间）
- [x] pytest 全部通过（推进时钟断言文件删除、状态标记、过期响应）

## Comments

- 2026-08-19: T06 完成。实现: `backend/cleaner.py` 独立模块 (DESIGN 规划) — `DeliveryCleaner` 周期线程 (60s) + `cleanup_expired()` 幂等扫描; TTL 按任务创建者身份快照 (免费 24h / 会员 72h, config 环境变量可覆盖 + `.env.example`); TTL 起点 = `completed_at` (文件就绪时刻, 非创建时刻); 过期流程: 删文件 (OSError 容错 + 日志, 不阻塞过期标记) → 标记 `expired` + `file_path` 置 None (SSE 事件 url 随即失效) + 明确 message; 直链 410 + 「交付链接已过期, 文件已清理」; 可注入时钟 `_now` (与 auth 同模式); main lifespan 启动/停止清理线程; 状态机文档同步 (failed 无交付资产保持终态, 仅 completed → expired)。测试: 6 个 pytest 全绿 (test_ttl_cleanup.py: 24h 过期 + 文件删除 + 410 响应 / 免费-会员 TTL 差异不误伤 / 幂等 / 周期线程缩短间隔验证 / 时钟注入); 全量 53 passed。顺带修复 fixture 缺陷: `fake_download` 固定 output.mp4 → 按 format_id 命名 (贴近生产 outtmpl, 多任务产出独立文件)。/code-review 双轴审查通过, 修复: unlink 无异常保护 (Windows PermissionError 会杀死清理线程且永不重启 → try/except OSError + logging) + DRY 收敛 (MEMBER_KEY/member_key fixture 移 conftest, member_headers 移 helpers) + CONTEXT.md 状态机同步。提交: `9839027`。
