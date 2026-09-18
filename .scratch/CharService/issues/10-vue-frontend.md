# 10 — Vue 前端（/chat + /admin）

**What to build:** 单 Vue 3 项目双路由（:10079）：**/chat** 用户聊天窗（EventSource 消费 SSE，事件渐进渲染 thinking / tool_call / tool_result / final，reasoning 折叠展示，取消按钮，转人工按钮，**结构化确认卡片**——渲染 08 的 `confirm_token`，用户点击后带凭据提交，断线重连用 `after_event_id` 续拉）；**/admin** 审批台 + 接管台（审批台：挂起退款列表含金额 / 订单上下文 / 用户历史退款次数，批准 / 拒绝；接管台：转人工会话历史 + 直接回复）。**角色区分**：审批端点在界面上体现主管 / 风控权限（客服角色不可见或只读），与 07 的后端 403 形成双重保障。

**Blocked by:** 09、**CharAgent P1-2**（取消语义：状态机置 `cancelled` + 资源释放，让 /chat 的取消按钮真的「生效」）

**Status:** ready-for-agent

- [ ] /chat：SSE 六类事件渐进渲染，reasoning 可折叠
- [ ] /chat：确认卡片可点击，凭据过期后给出明确提示并可重新发起
- [ ] /chat：取消按钮（kill switch）生效，转人工按钮接通
- [ ] /chat：断线重连 + `after_event_id` 续拉，不丢事件
- [ ] /admin 审批台：挂起列表 + 批准 / 拒绝全路径
- [ ] /admin 接管台：完整历史 + 回复
- [ ] 审批入口按角色区分（客服角色不可审批）
- [ ] 前端仅承载能力，不做视觉规范（动漫主题等属 charplot，不复用）
