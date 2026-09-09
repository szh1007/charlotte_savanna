"""真实 DeepSeek API 集成测试 (issue 01 + 02: RUN_INTEGRATION=1 可跑).

验证真实协议字段: 非流式 tool_calls 结构 / usage / finish_reason /
reasoning_content 分离, SSE 流式 delta 累积, tools wire 格式被真实端点接受,
以及 openai SDK 适配器在真实端点上 behavior 与 httpx 适配器一致
(SDK 的 extra 字段保留 reasoning_content, 流式 delta 同样累积).
默认跳过, 运行: RUN_INTEGRATION=1 pytest tests/integration/
(需根 .env 配置 DEEPSEEK_* 密钥)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from helpers import TOOL_SCHEMA

from CharAgent.model import (
    FinishReason,
    HttpXChatModel,
    OpenAIChatModel,
    chat_model_from_env,
    openai_chat_model_from_env,
)

# 根 .env 位于本文件向上三层: tests/integration -> tests -> CharAgent -> 仓库根
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

pytestmark = [
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="真实 API 集成测试需 RUN_INTEGRATION=1 (防止 CI 与日常测试触网计费)",
    ),
]

if not os.getenv("DEEPSEEK_API_KEY"):
    pytest.skip(
        "DEEPSEEK_API_KEY 未配置, 跳过真实 API 集成测试", allow_module_level=True
    )


def make_chat_model() -> HttpXChatModel:
    """复用 .env 的 DEEPSEEK_* 配置 (模型名前缀自动剥离, 见 chat_model_from_env)."""
    return chat_model_from_env()


def make_sdk_model() -> OpenAIChatModel:
    """SDK 适配器, 同一 .env 配置构建 (与 httpx 侧同配置, 便于对比行为)."""
    return openai_chat_model_from_env()


async def test_non_stream_text_fields() -> None:
    """非流式真实响应: content / finish_reason / usage / model 字段正确."""
    chat = make_chat_model()
    response = await chat.generate(
        [{"role": "user", "content": "用一句话解释什么是 idempotency key"}],
        temperature=0,
    )
    await chat.aclose()
    assert response.content and isinstance(response.content, str)
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None and response.usage.total_tokens > 0
    assert response.usage.input_tokens > 0
    assert response.model == chat.model  # 回显模型名与实际配置一致 (非硬编码)


async def test_non_stream_reasoning_content_separated() -> None:
    """deepseek-v4-flash 是推理模型: reasoning_content 真实存在且与正文分离 (#11)."""
    chat = make_chat_model()
    response = await chat.generate(
        [{"role": "user", "content": "17 乘以 23 等于多少?只回答数字"}],
        temperature=0,
    )
    await chat.aclose()
    assert response.reasoning  # 推理模型默认产出思维链 (#11 字段真实存在)
    assert response.content
    # reasoning 常复述答案, 分离语义是"两字段各自独立", 而非内容互斥
    assert response.content != response.reasoning


async def test_stream_delta_accumulation_matches_non_stream() -> None:
    """流式累积字段验证: content / reasoning / usage / finish_reason 齐全."""
    chat = make_chat_model()
    response = await chat.generate(
        [{"role": "user", "content": "用一句话解释什么是 idempotency key"}],
        temperature=0,
        stream=True,
    )
    await chat.aclose()
    assert response.content and isinstance(response.content, str)
    assert response.reasoning  # 流式 delta.reasoning_content 累积成功
    assert response.finish_reason is FinishReason.STOP
    assert (
        response.usage is not None and response.usage.total_tokens > 0
    )  # include_usage 生效


async def test_tools_wire_format_accepted() -> None:
    """tools JSON Schema 直通真实端点: 模型按要求返回 tool_calls 结构 (#10)."""
    chat = make_chat_model()
    response = await chat.generate(
        [
            {
                "role": "user",
                "content": (
                    "请调用 query_order 工具查询订单 20260701123456 的物流状态,"
                    " 不要输出任何文字, 只调用工具"
                ),
            }
        ],
        tools=[TOOL_SCHEMA],
        temperature=0,
    )
    await chat.aclose()
    assert response.has_tool_calls
    assert response.finish_reason is FinishReason.TOOL_CALLS
    call = response.tool_calls[0]
    assert call.name == "query_order"
    arguments = json.loads(call.arguments)  # arguments 是合法 JSON 字符串
    assert arguments["order_no"] == "20260701123456"


# ---------------------------------------------------------------------------
# issue 02: openai SDK 适配器在真实端点上的行为验证 (与 httpx 适配器同配置)
# ---------------------------------------------------------------------------


async def test_sdk_non_stream_reasoning_content_separated() -> None:
    """SDK 路径: 真实 reasoning_content 经 SDK extra 字段保留并分离 (#11)."""
    sdk = make_sdk_model()
    response = await sdk.generate(
        [{"role": "user", "content": "17 乘以 23 等于多少?只回答数字"}],
        temperature=0,
    )
    await sdk.aclose()
    assert response.reasoning  # SDK 未丢弃推理字段 (extra 保留)
    assert response.content
    assert response.content != response.reasoning
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None and response.usage.total_tokens > 0
    assert response.model == sdk.model  # SDK 回显模型名与实际配置一致


async def test_sdk_stream_accumulates_reasoning_and_usage() -> None:
    """SDK 流式: 真实 delta 分片 (含 reasoning_content) 经 chunk 累积为完整响应."""
    sdk = make_sdk_model()
    response = await sdk.generate(
        [{"role": "user", "content": "用一句话解释什么是 idempotency key"}],
        temperature=0,
        stream=True,
    )
    await sdk.aclose()
    assert response.content and isinstance(response.content, str)
    assert response.reasoning  # 流式 delta.reasoning_content 经 SDK chunk 累积成功
    assert response.finish_reason is FinishReason.STOP
    assert (
        response.usage is not None and response.usage.total_tokens > 0
    )  # include_usage 生效 (SDK 末 chunk usage)
    assert response.model == sdk.model
