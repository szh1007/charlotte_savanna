# 31-P2-7 — mcp + skills 插件

**What to build:** 能力扩展插件（P2）：MCP（Model Context Protocol）client + server——连接外部工具 server 的标准协议，三类能力（tools / resources / prompts）与三种传输（stdio / SSE / Streamable HTTP）（#50）；Agent Skills——渐进式披露加载的指令+脚本+模板+示例封装，SkillRouter 规则/语义/模型分层路由（#51）；与 MCP 互补（MCP 提供连接，Skill 提供知识）。延展范围：A2A（agent ↔ agent 协议 #52）、多模态输入（图像理解/OCR #53）、代码解释器（沙箱执行 + stdout/stderr/文件产物回填 #54）。基于核心 @tool 注册机制与 loop 挂载（ADR-0007）。

**Blocked by:** 03, 04

**Status:** ready-for-agent

- [ ] MCP client：接入外部工具 server（#50）
- [ ] MCP server：暴露本框架工具（#50）
- [ ] Agent Skills：渐进式披露（#51）
- [ ] SkillRouter：分层路由（规则/语义/模型）（#51）
- [ ] A2A：agent 间协作（Agent Card / 任务 / 传输）接入评估（#52）
- [ ] 多模态：图像输入 / OCR 类消息结构支持（#53）
- [ ] 代码解释器：沙箱执行 + stdout/stderr/文件产物回填（#54）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
