"""模型层 (model 包): 薄 ChatModel 协议 + httpx 裸调 / openai SDK 双适配器.

设计依据 (CharAgent/docs):
- ADR-0001: ChatModel 薄协议 ``generate(messages, tools) -> ModelResponse``,
  隔离「模型」与 runtime
- ADR-0003: 双适配器并列 —— httpx 裸调看清协议细节, openai SDK 贴近生产实际,
  两实现行为一致由契约测试约束 (issue 02)
- #11: reasoning_content 与正文分离, 有 / 无推理字段两分支均可解析
- #10: tool_calls 的 arguments 保持原始 JSON 字符串, 畸形 JSON 由工具执行层给
  可操作错误 (#2)
- #68: temperature / top_p / seed 调用级可配置, seed 固定后同输入同输出

messages 与 tools 使用 OpenAI 兼容 wire dict 直通 /chat/completions, 不引入中间
消息模型, SDK 适配器与 MockLLM (issue 09) 复用同一格式.

模块分工 (顶层 = 协议行为模块, utils/ = 静态支撑物, 便于审查):
- protocol.py       ChatModel 薄协议 (SPI, 双适配器与 MockLLM 实现同一协议)
- client_httpx.py   httpx 裸调适配器 + 环境变量工厂
- client_sdk.py     openai SDK 适配器 (响应 / chunk 对象 model_dump 回 wire 结构,
                    复用同一组解析纯函数保证与 client_httpx.py 行为一致)
- parse.py          响应解析纯函数 (非流式字段映射 + 错误体提取, 双适配器共用)
- stream.py         流式 delta 累积状态机 (内容 / reasoning / tool_calls 分片)
- utils/config.py   适配器共享配置: 默认端点 / 模型名 / provider 前缀剥离
- utils/errors.py   异常语义: 瞬态 / 永久区分 (retryable, 供 P0-5 retry 判断)
- utils/types.py    响应数据结构: FinishReason / ModelToolCall / Usage / ModelResponse
"""

from __future__ import annotations

from CharAgent.model.client_httpx import HttpXChatModel, chat_model_from_env
from CharAgent.model.client_sdk import OpenAIChatModel, openai_chat_model_from_env
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.errors import (
    ModelConfigError,
    ModelConnectionError,
    ModelError,
    ModelProtocolError,
    ModelStatusError,
    ModelTimeoutError,
)
from CharAgent.model.utils.types import (
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
    "OpenAIChatModel",
    "ToolSpec",
    "Usage",
    "chat_model_from_env",
    "openai_chat_model_from_env",
]
