# 12 — AI 视频总结: 转录 / 总结 / 思维导图 / AI 问答 (Phase 2)

**What to build:** 在 BilibiliDownloader 增加 AI 总结能力（PRD Phase 2「视频总结」启动, ADR-0005）：用户对已解析的 B 站视频发起总结 → 后端获取转录文本
（字幕快路径优先 + SenseVoice 转写兜底）→ LLM（DeepSeek）生成结构化总结
（章节时间线 + 要点大纲）→ 前端展示 总结视图（时间线 / 转录全文 / 思维导图）
+ AI 问答对话。总结任务并入现有任务体系（kind=`summary`, 状态机扩展
`transcribing → summarizing`, 复用 SSE / TTL / 会员身份 / 任务面板）; 付费
差异：免费每日配额（总结 3 / 问答 10, 按匿名 client_id 计数, 内存态重启清零）,
会员无限; 转录与总结随任务 TTL 清理, 提供 Markdown / TXT 导出。

**Blocked by:** None — can start immediately

**Status:** completed

**验收标准：**
- [x] 后端 transcript 管线: 字幕快路径（`BILI_COOKIE` 可选配置, 配置且有效时
      提取官方字幕）→ 回退 SenseVoice 转写（`funasr` + 音频流下载）; 未配置
      cookie 不报错, 直接走 ASR
- [x] `POST /api/summarize {url}` 创建总结任务, SSE 推送 `transcribing →
      summarizing → completed / failed` 进度（含 ASR 百分比）; 免费用户超每日
      配额返回 429, 会员不限
- [x] 总结结果: `GET /api/tasks/{id}/summary` 返回结构化总结（章节时间线 +
      要点, JSON）; `GET /api/tasks/{id}/transcript` 返回带时间戳转录文本
- [x] `POST /api/tasks/{id}/qa {question}` AI 问答（上下文 = 转录 + 总结, 单次
      塞入 LLM）; 免费每日配额 10 次, 会员不限
- [x] 导出: 总结 / 转录可下载 Markdown / TXT（用户本地保存, 与 TTL 无关）
- [x] TTL 清理: 总结任务与下载任务共用 cleaner, 转录 / 总结随 TTL 过期
- [x] 前端: 解析结果卡新增「AI 总结」按钮; 任务面板 summary 卡片带「查看总结」
      入口; 总结视图含 时间线 / 转录全文 / 思维导图（手写 CSS 树形, 零 UI 库）
      / 问答对话 / 导出按钮
- [x] 测试: HTTP seam 模式, mock 字幕提取 / ASR / LLM 三层; 配额 / 状态机 /
      TTL / 导出用例; pytest 全绿
- [x] 文档同步: CONTEXT / ADR-0005 / DESIGN / README / .env.example
      （`BILI_COOKIE` / `LLM_*` 占位）; PRD Phase 2 状态更新

## Comments

- 2026-08-20: issue 12 创建（T11~T15 分步实施中）。已确认决策（grill-with-docs
  会话, 2026-08-20）：① 内容获取 = 字幕优先（服务端自备 cookie 可选, 不收集
  用户凭据）+ SenseVoice 回退; ② LLM = DeepSeek（openai SDK, 不引入 LangChain）;
  ③ 4 项功能（总结 / 转录 / 思维导图 / 问答）一次交付; ④ 付费差异 = 免费每日
  限量（总结 3 / 问答 10）, 会员无限; ⑤ 架构 = 并入任务体系（kind=summary）;
  ⑥ 结果保留 = TTL 清理 + MD/TXT 导出。领域术语已入 CONTEXT.md, ADR-0005 已写。
- 2026-08-20: 全部验收项完成, issue 关闭。测试 116 全绿（含 test_summarize.py
  20 个新用例）, ruff 通过, 前端 `npm run build` 通过; funasr 1.4.2 已安装
  （torch 2.13.0+cpu / modelscope 预装环境）。状态机按 kind 分流
  （transcribing vs downloading）, DESIGN / CONTEXT / README / PRD / .env.example
  已同步, 术语统一为 `transcribing`。
- 2026-08-20: 测试数更正: /implement 自检查（双轴 code-review）后补 failed 路径与
  进度区间断言 3 个用例, 总数 116 → 119（test_summarize.py 23 个用例）; 文档
  PRD / README 同步补齐四功能（总结 / 转录 / 导图 / 问答）需求与使用说明, 修订
  说明与测试数同步为 119。
