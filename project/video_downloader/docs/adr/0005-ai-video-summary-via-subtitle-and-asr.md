# ADR-0005: AI 视频总结 — 字幕优先 + SenseVoice 回退, 并入任务体系

- 日期：2026-08-20
- 状态：已接受

## 背景

PRD Phase 2 计划「视频总结（LLM 能力）」启动。用户诉求：快速了解
长视频讲解的知识点（大纲、核心要点）。竞品调研（bibigpt.co / notegpt.io）验证
了两家均采用「平台字幕优先 + 无字幕走 ASR 转写」的路线；功能集为 总结（章节
时间线 + 要点）/ 转录全文 / 思维导图 / AI 问答。

技术事实：yt-dlp 的 B 站 extractor 提取 CC 字幕需要登录 cookie；B 站 AI 字幕
接口同样需要登录态。ASR 选型：本机 `.venv` 已装 torch (CPU) + modelscope,
FunASR/SenseVoice 底座现成, 中文质量优于 whisper 且 CPU 速度快数倍。

## 决策

1. **内容获取：字幕快路径优先, SenseVoice 转写兜底**。服务端在 `.env` 自备
   可选配置 `BILI_COOKIE`（服务端自己的 B 站账号 cookie）, 配置且有效时优先
   提取官方字幕（秒级）; 未配置 / 无字幕时回退 `funasr` 的 SenseVoice 模型
   本地转写（下载音频流 → ASR, 全覆盖, 进度经 SSE 可见）。**不收集、不使用
   任何用户提供的 cookie 或登录态**（用户凭据是安全红线）。
2. **LLM：DeepSeek, openai SDK 兼容调用**。子项目 `.env` 以 `LLM_API_KEY` /
   `LLM_BASE_URL` / `LLM_MODEL` 配置（模板见 `.env.example`）; 未配置
   `LLM_API_KEY` 时回退仓库根 `.env` 的 `DEEPSEEK_API_KEY`（模型名回退
   `DEEPSEEK_MODEL_NAME`）; 不引入 LangChain, 保持 video_downloader 零
   LLM 框架依赖。
3. **总结任务并入现有任务体系**。新增 kind=`summary`, 状态机扩展
   `transcribing → summarizing → completed / failed`; 复用 SSE 进度流、
   TTL 清理、会员身份、前端任务面板。Task 携带 transcript 与结构化总结。
4. **付费差异（后端强制）**：免费档每日配额（总结 3 次 / 问答 10 次, 按
   匿名 client_id 计数, 内存态重启清零）, 会员无限; 与现有「免费档真实受限」
   哲学一致。
5. **结果保留**：转录与总结随任务 TTL 清理（免费 24h / 会员 72h, 复用现有
   cleaner）, 用户可导出 Markdown / TXT 永久保存。
6. **AI 问答不建向量库**：上下文 = 该视频转录文本 + 总结, 单次塞入 LLM
   （DeepSeek v4-flash 128k 窗口覆盖 ≤3h 视频）, 超长截断提示。

## 后果

- 学习场景闭环：粘贴链接 → 转录 → 总结（时间线/要点/导图）→ 追问, 全程
  复用现有异步任务与进度流基建
- 无字幕视频仍可用（ASR 兜底）, 但转写耗时 5~15 分钟（CPU, 1h 视频）, 依赖
  SSE 进度缓解等待感
- 新增依赖：`openai`（轻）、`funasr`（依赖已装的 torch/modelscope）+ SenseVoice
  模型下载约 1GB; 首次转写触发模型下载
- `BILI_COOKIE` 为敏感信息: 仅入 `.env`（不提交）, 账号有平台封号风险
  （与现有「封号风险自担」声明同性质）; 不配置则功能降级为纯 ASR, 不报错
- 内存态存储随 ADR-0003: 总结结果服务重启即失, 导出为用户自行保存手段
