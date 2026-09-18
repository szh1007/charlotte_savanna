# 24-P1-14 — 前端 Vue 双路由（/chat + /admin）

> **2026-09-18：已整个移交 `CharService`，本 issue 不再执行。**
> 前端界面（/chat + /admin）完全是客服业务，不属于业务无关的框架包（ADR-0008 决策 9 的分层剥离）。
> 移交后并**扩展**：新增结构化确认卡片（ADR-0009）与审批台角色区分。见 `.scratch/CharService/issues/10-vue-frontend.md`。
> 下方原文保留作为设计演进记录。

**What to build:** 单 Vue 项目双路由（Vue 3 + EventSource）：/chat 用户聊天窗（SSE 事件渐进渲染：thinking/tool_call/tool_result/final 可视化、reasoning 折叠展示、取消按钮、转人工按钮）；/admin 审批台 + 接管台（挂起审批列表含上下文、批准/拒绝；转人工会话接管：历史 + 回复）。断线重连（EventSource 自动重连 + after_event_id 续拉）。

**Blocked by:** 11, 23

**Status:** transferred → `CharService`

- [ ] /chat：事件渐进渲染（四类事件 + reasoning 折叠）（#4）
- [ ] /chat：取消按钮（POST cancel）、转人工按钮（POST escalate）
- [ ] /admin：审批列表（上下文可见）+ 批准/拒绝（POST approve/reject）（#25）
- [ ] /admin：接管台（GET messages 历史 + POST reply）（#19）
- [ ] 断线重连：EventSource 重连 + 事件续拉不丢事件
