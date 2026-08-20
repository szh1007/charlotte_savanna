# 13 — 字幕来源可选: 全局设置 + 模型预下载 + 模型字幕缓存 (Phase 3)

**What to build:** 把当前自动的字幕获取链路改为用户可选（PRD Phase 3 启动, ADR-0006）：
前端提供全局字幕来源设置（官方字幕 / 模型生成字幕, 二选一, localStorage 持久化,
默认官方字幕, 位于下载记录右侧）, 创建总结任务时把当前选择作为创建者快照传入
`subtitle_source`（缺省 official, 向后兼容）。模型可提前下载（独立入口 + 状态展示
+ SSE 进度, 下载到项目 `models/` 目录, 全局唯一下载状态机 missing / downloading /
ready）。转录链路分场景触发：选官方字幕 → 服务端 cookie 秒级提取, 结果为空时自动
切换模型生成（仅校验模型存在, 缺失则转录任务明确失败并提示先下载, 不自动触发
1GB 下载）; 选模型生成字幕 → 优先命中字幕缓存（`models/subtitles/<BV>.json`,
分 P 加 `_pN` 后缀, 按创建者身份 TTL 免费 24h / 会员 72h, 全局共享命中, 命中不另扣
配额, 官方字幕不缓存）, 未命中则转写并写缓存; 模型缺失时由转录子任务自动触发下载
（显示「模型下载中 x%」, 下载完成自动继续转写, 任务取消不中断下载）。过期字幕缓存
由 cleaner 周期清理, 模型本体不清理。

**Blocked by:** None — can start immediately

**Status:** done

**验收标准：**
- [x] API 契约: `POST /api/summarize` 新增 `subtitle_source`（`official` / `model`,
      缺省 `official` 向后兼容）; `GET /api/model/status` → `{status, progress,
      has_official_subtitle}`（`has_official_subtitle` = 服务端是否配置 cookie）;
      `POST /api/model/download` 幂等; SSE 新增 `model-update` 事件（与任务事件
      同一条流, 不受 task_id 过滤影响）
- [x] 转录双路径: 选官方字幕 → cookie 提取官方字幕（秒级, 不写缓存）; 官方字幕
      为空 → 自动切换模型生成, 仅校验模型存在（缺失 → 转录 failed 且错误信息
      含「请先下载模型」, 不自动触发下载）; 选模型生成 → 先查字幕缓存, 未命中
      则转写
- [x] 模型状态机: `missing / downloading / ready` 三态 + 进度; ready 判定 = 模型
      配置与权重文件均存在（`config.yaml` + `model.pt`）; 重复触发下载幂等
      （已就绪不动作 / 下载中返回当前进度）; 下载失败回 missing 可重试; 下载到
      项目 `models/` 目录（env 可覆盖）
- [x] 自动下载联动: 选模型生成且模型缺失 → 转录子任务自动触发下载, `transcribing`
      阶段显示「模型下载中 x%」（进度可见）, 下载完成自动继续转写; 取消总结任务
      不中断模型下载
- [x] 字幕缓存: 文件名 = 视频 BV 号（URL 带 `p=N` 且 N>1 时加 `_pN` 后缀, 分 P
      隔离不串味）; 内容 = 转录段 JSON + `created_at` + `is_member`; 过期判定 =
      `now - created_at < delivery_ttl(is_member)`（免费 24h / 会员 72h, 与交付
      TTL 同源）; 全局共享命中, 命中不另扣配额; BV 号解析失败则本次跳过缓存
      （不查不写）, 不阻塞转录
- [x] 清理: cleaner 周期扫描字幕缓存目录, 按文件内创建者身份 TTL 删除过期文件
      （幂等, 可注入时钟）; 模型本体不清理（持久资产）; `models/` 入 .gitignore,
      `MODELS_DIR` / `SUBTITLES_DIR` 入 .env.example
- [x] 前端: 全局设置 radio（官方字幕 / 模型生成字幕, localStorage 持久化, 默认
      official, 位于下载记录右侧）; 模型状态展示 + 下载按钮 + 进度（SSE
      model-update 订阅）; 服务端未配置 cookie 时提示「官方字幕不可用, 将自动
      切换模型生成」, 不阻止创建; 创建总结任务时携带当前字幕来源
- [x] 测试: 沿用 HTTP seam 模式 + 引擎 mock（模型下载引擎为独立调用点替换, 驱动
      状态机流转 / 进度回调 / 失败回 missing / 并发幂等）+ 缓存目录指向 tmp_path
      真实 IO + cleaner 时钟注入; 覆盖: 官方路径不写缓存 / 官方为空 + 模型存在 →
      回退转写并写缓存 / 官方为空 + 模型缺失 → 转录 failed 且提示先下载 / model
      路径命中缓存 → ASR 不被调用 / 未命中 → 转写 + 写缓存 / 模型缺失 → 下载被
      触发且完成后转写 / 分 P 键隔离 / 免费与会员 TTL 过期边界 / 命中缓存不扣
      配额 / 模型 API 幂等与状态流转 / cleaner 清理过期缓存且不碰模型本体;
      pytest 全绿
- [x] 文档同步: DESIGN.md 内容获取管线章节更新; CONTEXT.md / ADR-0006 / PRD
      Phase 3 状态更新（术语与决策已就绪）

## Comments

- 2026-08-21: issue 13 创建（to-tickets, 单 issue 交付）。需求与用户故事见
  `.scratch/video-downloader/PRD.md`「字幕来源与模型下载（Phase 3, issue 13 +
  ADR-0006）」分组（24 条用户故事, Testing Decisions 含接缝清单）, 决策见
  `project/video_downloader/docs/adr/0006-subtitle-source-and-model-download.md`。
  未实施。
- 2026-08-21: 已实施交付。后端（双路径 / 模型状态机 / 缓存 / cleaner / SSE
  model-update）测试 183 全绿（新增 test_model 10 + test_subtitle_source 12,
  基线无回归）; 前端（全局字幕来源设置 + 模型状态/下载/进度 + Cookie 提示）
  `npm run build` 通过; DESIGN.md 新增 Phase 3 章节, CONTEXT.md / ADR-0006 /
  PRD 已同步。
