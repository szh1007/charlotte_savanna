# ADR-0006: 字幕来源可选 — 全局设置 + 模型预下载 + 模型字幕缓存

- 日期：2026-08-21
- 状态：已接受

## 背景

ADR-0005 的转录链路完全自动：有 `BILI_COOKIE` 走官方字幕快路径, 否则首次转写
自动下载 SenseVoice 模型（约 1GB, 落 modelscope 用户缓存）后本地转写。用户诉求：
获得字幕来源选择权（官方字幕 vs 模型生成字幕, 全局设置而非逐任务选择）、支持
提前下载模型、模型生成的字幕按 TTL 缓存避免重复转写。

技术事实：modelscope `snapshot_download(local_dir=...)` 支持直接下载到指定目录
并回调进度（本机 modelscope 1.39.1）; `funasr.AutoModel(model=本地路径)` 可加载
本地模型; 本机无既有 modelscope 缓存, 模型位置可干净统一到项目 `models/` 目录。

## 决策

1. **全局字幕来源设置, 创建任务时快照传参**。前端全局二选一（官方字幕 / 模型
   生成字幕, localStorage 持久化, 默认官方字幕）, `POST /api/summarize` 携带
   `subtitle_source`（缺省 official, 向后兼容）。后端不存全局状态（无登录体系,
   匿名 client_id）。
2. **模型下载到项目 `models/` 目录, 全局唯一下载状态机**。`GET /api/model/status`
   （missing / downloading / ready）/ `POST /api/model/download`（幂等）; 进度经
   SSE 新增 `model-update` 事件推送（与任务事件同流）。ready 判定：
   `models/SenseVoiceSmall/` 下 `config.yaml` + `model.pt` 存在。
3. **按需触发语义分场景**。主动选「模型生成字幕」且模型缺失 → 转录子任务自动
   触发下载（进度可见, 任务取消不中断下载, 模型是全局资产）; 官方字幕为空回退
   模型生成 → 只校验模型存在, 缺失则转录子任务失败并提示先下载（不自动触发
   1GB 下载）。
4. **模型字幕缓存, 按创建者身份 TTL**。缓存文件 `<BV>.json`（分 P 加 `_pN` 后缀
   防串味）, 内容为转录段 JSON + `created_at` + `is_member`; 过期判定 =
   `now - created_at < delivery_ttl(is_member)`（免费 24h / 会员 72h, 与交付 TTL
   同源）。全局共享命中, 命中不另扣配额（配额按任务计）。官方字幕不缓存（秒级
   获取）。
5. **缓存清理并入现有 cleaner**; 模型本体不清理（持久资产）。`models/` 入
   .gitignore。

## 后果

- 转录子任务扩展为三态来源 + 缓存读写 + 模型下载联动, 测试面扩大（缓存命中 /
  过期 / 分 P 键 / 下载触发 / 回退校验）
- 模型资产管理：下载中断残留由重触发续传覆盖, 无取消能力（YAGNI）
- BV 号来自任务创建时轻量解析（失败则本任务跳过缓存, 不阻塞转录）
- `BILI_COOKIE` 未配置时全局选官方字幕必然回退, 前端经 `has_official_subtitle`
  提示, 不阻止创建
