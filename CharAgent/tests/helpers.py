"""CharAgent 测试共享常量与工具样本 (跨测试文件复用, 避免重复定义).

- API_KEY / BASE_URL / CHAT_URL: respx 拦截 httpx mock 用的端点常量.
- TOOL_SCHEMA: 真实端点接受的工具 wire 样本 (tools 参数直通 /chat/completions).
"""

from __future__ import annotations

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
