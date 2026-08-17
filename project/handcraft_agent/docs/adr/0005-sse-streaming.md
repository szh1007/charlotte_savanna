# ADR-0005: 流式输出用 SSE，而非 WebSocket

- 状态: accepted
- 日期: 2026-08-18（自 README 选型 0005 拆出）
- 考虑过的方案: WebSocket——拒绝，双向能力多余，还引入协议升级、心跳、重连、LB 粘性成本；轮询——拒绝，延迟高、浪费请求
- 后果: 客户端用 EventSource 消费；双向交互（如 HITL 需客户端主动推送）需单独处理（走独立 REST 端点）

所有流式输出（agent 进度、工具调用事件、token 流）统一用 SSE。agent 流式输出是服务器→客户端的单向推送，SSE 的单向性刚好匹配；且 LLM 流式 API（OpenAI/Anthropic）本身返回 SSE 格式，可直通转发。
