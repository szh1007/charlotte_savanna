"""模型层 (model 包): 薄 ChatModel 协议 + DeepSeek httpx 裸调适配器 (OpenAI 兼容).

设计依据 (CharAgent/docs):
- ADR-0001: ChatModel 薄协议 ``generate(messages, tools) -> ModelResponse``,
  隔离「模型」与 runtime
- ADR-0003: httpx 裸调自己拼 /chat/completions, 与 openai SDK 适配器 (issue 02)
  保持行为一致
- #11: reasoning_content 与正文分离, 有 / 无推理字段两分支均可解析
- #10: tool_calls 的 arguments 保持原始 JSON 字符串, 畸形 JSON 由工具执行层给
  可操作错误 (#2)
- #68: temperature / top_p / seed 调用级可配置, seed 固定后同输入同输出

messages 与 tools 使用 OpenAI 兼容 wire dict 直通 /chat/completions, 不引入中间
消息模型, SDK 适配器 (issue 02) 与 MockLLM (issue 09) 复用同一格式.

模块分工 (按用途拆分, 便于审查):
- types.py     响应数据结构: FinishReason / ModelToolCall / Usage / ModelResponse
- errors.py    异常语义: 瞬态 / 永久区分 (retryable, 供 P0-5 retry 判断)
- parsing.py   非流式响应解析 (纯函数, 无网络依赖可直接单测)
- sse.py       SSE 流式 delta 累积状态机 (内容 / reasoning / tool_calls 分片)
- protocol.py  ChatModel 薄协议 (SPI, MockLLM 与 SDK 适配器实现同一协议)
- http.py      httpx 裸调适配器 + 环境变量工厂
"""

from __future__ import annotations

from CharAgent.model.errors import (
    ModelConfigError,
    ModelConnectionError,
    ModelError,
    ModelProtocolError,
    ModelStatusError,
    ModelTimeoutError,
)
from CharAgent.model.http import HttpXChatModel, chat_model_from_env
from CharAgent.model.protocol import ChatModel
from CharAgent.model.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "ChatModel",
    "FinishReason",
    "HttpXChatModel",
    "ModelConfigError",
    "ModelConnectionError",
    "ModelError",
    "ModelMessage",
    "ModelProtocolError",
    "ModelResponse",
    "ModelStatusError",
    "ModelTimeoutError",
    "ModelToolCall",
    "ToolSpec",
    "Usage",
    "chat_model_from_env",
]
