# ⑨ 能力扩展（#50-54）

> 返回 [难点清单索引](../README.md#难点清单)

| ID | 难点 | 详细细节 | 阶段 |
|----|------|----------|------|
| 50 | MCP 集成 | • MCP client：连接外部 MCP server，把外部提供的工具接入 agent | P2 |
| | | | • MCP server 开发：把自己的工具封装成 MCP server，供别的 agent 调用 | |
| | | | • 三种传输方式：stdio（本地进程标准输入输出）/ SSE（早期远程传输）/ Streamable HTTP（现行推荐标准，替代 SSE、解决长连接管理问题） | |
| | | | • 三类能力，不只是 tools：tools（工具）/ resources（只读资源，如文件内容、数据库记录）/ prompts（提示词模板） | |
| | | | • 生命周期：initialize（握手、协商协议版本与能力）→ tools/list（发现工具）→ tools/call（调用工具） | |
| | | | • sampling：MCP server 可以反向请求 client 调用 LLM（agent 当 client 时，server 反过来让 agent 帮忙生成内容） | |
| | | | • 工具发现 / 权限 / 多 server 路由：多个 MCP server 的工具如何被发现、授权、路由，同名工具如何仲裁 | |
| 51 | Agent Skills | • SKILL.md 格式：技能的元数据 + 指令的固定结构 | P2 |
| | | | • 渐进式披露三级加载：metadata（先看名字 / 描述）→ instructions（用到时加载）→ resources（深入时加载），省 token | |
| | | | • Skill 路由：skill 多时（几十上百）不能全量注入，用分层路由——L1 规则粗筛（关键词 / 意图）→ L2 语义召回（embedding 相似度取 top-k）→ L3 模型终选（在 top-k 里拍板） | |
| | | | • description 质量决定路由准确性：description 是路由的「索引」，要写清何时用、解决什么、能力边界、排除场景 | |
| | | | • 选错检测与冲突仲裁：模型调了不相关 skill 要能发现并让它重选；多个 skill 都相关时如何仲裁 | |
| | | | • 版本化 / 热更新 / 依赖 / 权限：skill 更新后运行中的 agent 切新版；skill 之间的依赖加载顺序；某些 skill 需特殊权限 | |
| | | | • 与 MCP / function calling 的区别：MCP 提供「连接」（怎么调到工具），Skill 提供「知识」（怎么做好某类任务），两者互补 | |
| 52 | A2A（Agent-to-Agent）协议 | • Agent Card：声明 agent 的能力、端点、鉴权方式 | P2 |
| | | | • 任务与消息：A2A 的核心交互单元 | |
| | | | • 传输：HTTP + JSON-RPC（基于 JSON 格式的远程过程调用协议）/ SSE | |
| | | | • 与 MCP 的区别：MCP 是 agent ↔ 工具，A2A 是 agent ↔ agent | |
| | | | • 多 agent 协作场景下的发现、寻址、编排 | |
| 53 | 多模态 agent | • 视觉输入：图像理解、OCR、截图分析 | P2 |
| | | | • 多模态工具：图像生成、音视频处理工具挂载 | |
| | | | • 多模态消息结构：image 字段的 message 格式 | |
| | | | • 应用场景：客服（用户发截图）、computer use（让模型像人一样操作电脑完成任务的范式） | |
| 54 | 代码解释器 / 工具执行环境 | • E2B（云端代码执行沙箱服务）/ Docker 沙箱 / 子进程隔离（关联 #24 沙箱） | P2 |
| | | | • 代码解释器：让 agent 生成并执行代码（数据分析、文件处理） | |
| | | | • 执行结果回填：stdout / stderr / 文件产物回填给模型 | |
| | | | • 资源限制与超时：CPU / 内存 / 磁盘限制，防止沙箱被拖垮 | |
