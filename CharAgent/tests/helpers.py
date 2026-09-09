"""CharAgent 测试共享常量与工具样本 (跨测试文件复用, 避免重复定义).

- API_KEY / BASE_URL / CHAT_URL: respx 拦截 httpx mock 用的端点常量.
- TOOL_SCHEMA: 真实端点接受的工具 wire 样本 (tools 参数直通 /chat/completions).
- text_completion_json / sse_chunk: 双适配器测试共用的响应样本工厂
  (契约测试「同一 mock 响应喂两适配器」依赖同源样本保证对比公平).
"""

from __future__ import annotations

import json
from typing import Any

API_KEY = "sk-test-charagent"
BASE_URL = "https://api.deepseek.com"
CHAT_URL = f"{BASE_URL}/chat/completions"

# 单工具 JSON Schema (P0-2 @tool 装饰器产出的同构 wire 格式)
TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_order",
        "description": "按订单号查询订单状态与物流信息",
        "parameters": {
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "14 位订单号"}
            },
            "required": ["order_no"],
            "additionalProperties": False,
        },
    },
}


def text_completion_json(*, reasoning: str | None = "核对订单号") -> dict[str, Any]:
    """贴近真实 DeepSeek 的非流式响应结构 (reasoning_content 可选, 默认带推理字段)."""
    message: dict[str, Any] = {"role": "assistant", "content": "订单已发货"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-mock-001",
        "model": "deepseek-v4-flash",
        "choices": [
            {"index": 0, "message": message, "logprobs": None, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 89, "completion_tokens": 12, "total_tokens": 101},
    }


def sse_chunk(payload: dict[str, Any]) -> str:
    """单行 SSE data 块 (OpenAI 流式约定)."""
    return f"data: {json.dumps(payload)}\n\n"
