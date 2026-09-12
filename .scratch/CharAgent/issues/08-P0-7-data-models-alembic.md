# 08-P0-7 — 五实体数据模型 + alembic 初始化

**What to build:** 五核心实体（Thread / Run / Message / ToolCall / Checkpoint）数据模型定义，P0 一次定死（含 schema 版本号向前兼容），Postgres 表 + alembic 首次迁移初始化。字段与协议定型一致：Run 状态机（created/running/waiting_tool/waiting_user/retrying/failed/finished/cancelled）、Message.reasoning 独立成列（供重连重建，前端折叠展示；注意它**同时**回填 wire 历史，见 issue 04 §1 修正后的 #11 契约）、ToolCall.status 含 needs_approval（HITL）、Checkpoint.parent_id 分支来源。demo 表（tickets/escalations/approvals/audit_logs）与幂等表预留为 P1 迁移。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 五实体模型定义完成，字段与协议定型一致（#5/#12 实体部分）
- [ ] Postgres DDL 生成（alembic），首次迁移在空库可执行
- [ ] 实体字段与状态机枚举一致（Run/ToolCall status 全部取值）
- [ ] 迁移体系预留 P1 demo 表与 P2 event 表追加路径（#12）
- [ ] 会话消息与 agent transcript 分层落库：`GET /threads/{id}/messages` 只应返回一问一答（user 提问 + `LoopResult.content`）；agent 内部 transcript（含 role=system 的续写指令、role=tool 的工具回填）归 Checkpoint / 观测，不可直接喂前端。注意 CONTINUE 场景 `content` ≠ `messages[-1].content`（前者是截断续写的跨段拼合结果）
