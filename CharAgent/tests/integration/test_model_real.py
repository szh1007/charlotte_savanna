"""真实 DeepSeek API 集成测试 (marker: integration).

验证真实协议字段: 非流式 tool_calls 结构 / usage / finish_reason /
reasoning_content 分离, SSE 流式 delta 累积, tools wire 格式被真实端点接受,
以及 openai SDK 适配器在真实端点上 behavior 与 httpx 适配器一致
(SDK 的 extra 字段保留 reasoning_content, 流式 delta 同样累积).
默认排除 (pytest.ini 的 addopts = -m "not integration"), 运行:
    pytest -m integration
(需根 .env 配置 DEEPSEEK_* 密钥; 调真实端点, 会消耗额度)
"""

from __future__ import annotations

import asyncio
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

pytestmark = pytest.mark.integration

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
    """deepseek-flash 是推理模型: reasoning_content 真实存在且与正文分离 (#11)."""
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


async def test_real_usage_reports_reasoning_tokens() -> None:
    """真实 usage 的 reasoning_tokens 落在 completion_tokens_details 下 (#11 成本).

    本用例是官方 schema 的活体校验: 若上游把该字段挪回顶层 (或改名),
    这里会红 —— 而非静默地把 reasoning_tokens 一直读成 None.

    2026-09-12 实测 (deepseek-flash, 思考模式默认开启) 的真实 usage 形状::

        {"prompt_tokens": 46, "completion_tokens": 222, "total_tokens": 268,
         "prompt_tokens_details": {"cached_tokens": 0},
         "completion_tokens_details": {"reasoning_tokens": 217},
         "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 46}

    顶层**没有** reasoning_tokens (修复前读顶层, 该字段恒为 None);
    且 completion_tokens 中 217/222 是思维链 —— 思考模式是成本大头.
    """
    chat = make_chat_model()
    response = await chat.generate(
        [{"role": "user", "content": "9.11 和 9.8 哪个大?只回答结论"}],
        temperature=0,
    )
    await chat.aclose()
    assert response.reasoning  # 思考模式默认开启, 确有思维链
    usage = response.usage
    assert usage is not None
    assert usage.reasoning_tokens  # 非 None 且 > 0
    assert usage.cache_hit_tokens is not None  # 缓存计量字段真实存在
    assert usage.cache_miss_tokens is not None
    assert usage.cache_hit_tokens + usage.cache_miss_tokens == usage.input_tokens


# 稳定的长前缀: 字节级一致, 跨请求可完整匹配缓存前缀单元 (不用随机内容 ——
# 前缀变一个字符就整块不命中, 用例会假红)
_CACHE_PREFIX = "\n".join(
    f"第 {i} 条: 上下文硬盘缓存按缓存前缀单元完整匹配命中, 前缀复用不计未命中。"
    for i in range(1, 81)
)

# 官方明示缓存是「尽力而为」且落盘耗时秒级, 单次断言会假红 —— 退避重试
_CACHE_ATTEMPTS = 4
_CACHE_BACKOFF_SECONDS = 3.0


async def test_real_prompt_cache_hit_reported() -> None:
    """缓存命中路径: 同一前缀重复请求, prompt_cache_hit_tokens 真实 > 0 (#11 成本).

    官方「例一」形状 —— 后续请求完整匹配前一轮的缓存前缀单元即可命中. 本用例
    用逐字节相同的 system + 长 user 前缀重复发送, 覆盖单测构造样本之外的
    **真实命中**路径 (命中输入单价约为未命中的 1/50, 成本核算依赖该区分).

    两处官方约束决定了用例形态:
    - 缓存构建耗时**秒级** -> 首轮多半 miss, 需要退避等待再重试;
    - 缓存系统**尽力而为, 不保证 100% 命中** -> 因此重试若干轮, 且末轮的
      失败信息会点明这一前提, 避免后来者把它当成解析 bug 去查.

    命中后仍断言恒成立的不变量 hit + miss == prompt_tokens (官方声明),
    保证即使一直未命中, 该不变量也始终在约束解析正确性.

    2026-09-12 实测 (deepseek-flash, 同前缀重发两轮)::

        第 1 轮: input=2011  hit=   0  miss=2011   # 首轮全 miss, 缓存尚未落盘
        第 2 轮: input=2011  hit=1792  miss= 219   # 89.1% 前缀复用

    两轮 hit + miss == input 均成立 —— 证实字段读取路径正确, 且首轮 miss
    是缓存的固有时序而非字段缺失 (A1 排查时那次 0/46 同理).
    """
    chat = make_chat_model()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "你是一位严谨的技术文档助手, 只回答问题本身."},
        {"role": "user", "content": f"{_CACHE_PREFIX}\n\n请只回复两个字: 收到"},
    ]
    hit_samples: list[int] = []
    try:
        for attempt in range(_CACHE_ATTEMPTS):
            # thinking=False: 本用例只关心输入侧缓存计量, 关思考省 token 与耗时
            response = await chat.generate(messages, temperature=0, thinking=False)
            usage = response.usage
            assert usage is not None
            assert usage.input_tokens is not None
            # 恒成立不变量 (官方: prompt_tokens == hit + miss) —— 无论是否命中
            assert usage.cache_hit_tokens + usage.cache_miss_tokens == (
                usage.input_tokens
            )
            assert usage.cache_hit_tokens > 0 or usage.cache_miss_tokens > 0
            hit_samples.append(usage.cache_hit_tokens)
            if usage.cache_hit_tokens > 0:
                break  # 命中路径已拿到, 不必继续烧额度
            if attempt < _CACHE_ATTEMPTS - 1:
                # 等缓存落盘 (官方称秒级), 退避递增
                await asyncio.sleep(_CACHE_BACKOFF_SECONDS * (attempt + 1))
    finally:
        await chat.aclose()

    assert max(hit_samples) > 0, (
        f"{_CACHE_ATTEMPTS} 轮同前缀请求均未命中缓存 (hit 序列: {hit_samples}). "
        f"官方明示缓存为「尽力而为, 不保证 100% 命中」, 未必是解析缺陷 —— "
        f"先重跑一次确认; 若持续复现, 再检查 prompt_cache_hit_tokens 的读取路径."
    )


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
# openai SDK 适配器在真实端点上的行为验证 (与 httpx 适配器同配置)
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
