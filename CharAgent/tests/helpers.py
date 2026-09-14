"""CharAgent 测试共享常量与工具样本 (跨测试文件复用, 避免重复定义).

- API_KEY / BASE_URL / CHAT_URL: respx 拦截 httpx mock 用的端点常量.
- TOOL_SCHEMA: 真实端点接受的工具 wire 样本 (tools 参数直通 /chat/completions).
- text_completion_json / sse_chunk: 双适配器测试共用的响应样本工厂
  (契约测试「同一 mock 响应喂两适配器」依赖同源样本保证对比公平).
- make_state / make_metadata / make_checkpoint: checkpoint 样本工厂 (issue 07) ——
  序列化协议、三个存储实现、跨实现对比这几组用例都拿同一份样本, 对比才有意义;
  编号与时刻是常量, 于是「同样的输入 → 一模一样的结果」(#61 确定性) 成立.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointMetadata,
    CheckpointSource,
    CheckpointState,
)

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
        "model": "deepseek-flash",
        "choices": [
            {"index": 0, "message": message, "logprobs": None, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 89, "completion_tokens": 12, "total_tokens": 101},
    }


def sse_chunk(payload: dict[str, Any]) -> str:
    """单行 SSE data 块 (OpenAI 流式约定)."""
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# checkpoint 样本 (issue 07)
# ---------------------------------------------------------------------------

# 一轮完整的对话样本: 用户提问 -> 模型要调工具 -> 工具结果回填
SAMPLE_MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": "订单 20260701123456 到哪了"},
    {
        "role": "assistant",
        "content": "我先查一下这笔订单",
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "query_order",
                    "arguments": '{"order_no": "20260701123456"}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_0", "content": "订单已发货"},
]

# 快照里的时刻固定成常量 (断言与快照对比都要「同输入同结果」, #61)
SAMPLE_CREATED_AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def make_state(**overrides: Any) -> CheckpointState:
    """造一份固定的**进度**样本 (要改哪个字段就传哪个, 别在用例里手抄整份).

    Args:
        **overrides: 想改的字段 (如 messages=..., turn_count=3, suspension=...).

    Returns:
        CheckpointState: 内容固定的进度 (深拷贝, 用例改它不影响别的用例).
    """
    fields: dict[str, Any] = {
        "messages": deepcopy(SAMPLE_MESSAGES),
        "turn_count": 1,
        "total_tokens": 128,
        "truncation_count": 0,
        "content_parts": [],
        "suspension": None,
    }
    fields.update(overrides)
    return CheckpointState(**fields)


def make_metadata(**overrides: Any) -> CheckpointMetadata:
    """造一份固定的**观察值**样本 (来源 / 本轮用量 / 工具 / 结束原因)."""
    fields: dict[str, Any] = {
        "source": CheckpointSource.LOOP,
        "turn_tokens": 64,
        "turn_elapsed_ms": 12.5,
        "tool_names": ["query_order"],
        "content": "订单已发货",
        "finish_reason": "stop",
        "outcome": "finished",
    }
    fields.update(overrides)
    return CheckpointMetadata(**fields)


def make_checkpoint(**overrides: Any) -> Checkpoint:
    """造一帧固定的快照样本 (编号与时刻是常量, 便于比快照、比相等).

    Args:
        **overrides: 想改的字段 (如 thread_id=..., state=..., parent_id=...).

    Returns:
        Checkpoint: 一帧快照 (走 Checkpoint.create 造, 于是校验规则也一起生效).
    """
    fields: dict[str, Any] = {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "turn_number": 1,
        "state": make_state(),
        "metadata": make_metadata(),
        "parent_id": None,
        "checkpoint_id": "ck-1",
        "created_at": SAMPLE_CREATED_AT,
    }
    fields.update(overrides)
    return Checkpoint.create(**fields)
