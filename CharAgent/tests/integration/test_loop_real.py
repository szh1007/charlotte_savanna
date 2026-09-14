"""真实 DeepSeek API 集成测试: agent loop 多轮工具路径 (marker: integration).

聚焦 scripted 测试的固有盲区: ScriptedModel 是 fake, 不校验 API wire 契约 ——
「缺 reasoning_content 导致真实端点 400」这类问题在 unit 层永远测不出来
(issue 04 提交时即如此, 2 轮以上工具路径无任何真实端点防线)。

关键契约 (DeepSeek 思考模式): 官方文档要求请求携带 tools 时, 后续**所有**请求
须完整回传历史轮次的 reasoning_content (文档称缺失即 400), 且会被拼接进上下文。
本机实测 (2026-09-11, deepseek-flash) 缺失未触发 400 —— 框架仍按文档执行以保留
交错思考, 本文件验证该执行路径在真实端点上可用。

双适配器分别验证 —— 防止某一侧在序列化时丢弃该字段。

默认排除 (pytest.ini 的 addopts = -m "not integration"), 运行:
    pytest -m integration
(需根 .env 配置 DEEPSEEK_* 密钥; 调 真实端点, 会消耗额度)

改动 model / agent 层后必须手动跑一次 —— 本文件的存在就是因为
ScriptedModel 不校验 API wire 契约, unit 层测不出真实端点的拒绝。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from dotenv import load_dotenv

from CharAgent.agent import AgentLoop, LoopGuard, LoopOutcome
from CharAgent.model import (
    FinishReason,
    HttpXChatModel,
    OpenAIChatModel,
    chat_model_from_env,
    openai_chat_model_from_env,
)
from CharAgent.tool import tool

# 根 .env 位于本文件向上三层: tests/integration -> tests -> CharAgent -> 仓库根
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

pytestmark = pytest.mark.integration

if not os.getenv("DEEPSEEK_API_KEY"):
    pytest.skip(
        "DEEPSEEK_API_KEY 未配置, 跳过真实 API 集成测试", allow_module_level=True
    )

ChatModelFactory = Callable[[], HttpXChatModel | OpenAIChatModel]


def _lookup_order(order_no: str) -> str:
    """按订单号查询订单状态与物流信息."""
    return f"订单 {order_no} 已发货, 预计 3 日内送达"


LOOKUP_TOOL = tool(_lookup_order, name="lookup_order")

USER_PROMPT = (
    "请调用 lookup_order 工具查询订单号 20260701123456 的状态,"
    " 然后把查询结果用一句话告诉我"
)


async def test_loop_multi_turn_tool_path_httpx() -> None:
    """httpx 适配器: 真实端点跑完整多轮工具路径 (turn 2 回传 turn 1 reasoning)."""
    await _assert_multi_turn_tool_path(chat_model_from_env)


async def test_loop_multi_turn_tool_path_sdk() -> None:
    """openai SDK 适配器: 同上 —— SDK 序列化不得丢弃 reasoning_content."""
    await _assert_multi_turn_tool_path(openai_chat_model_from_env)


async def _assert_multi_turn_tool_path(make_model: ChatModelFactory) -> None:
    """跑「模型决策 → 真实工具执行 → 回填 → 模型二次决策」并断言端点接受.

    turn 2 的请求包含 turn 1 的 assistant 消息 (带 tool_calls 与
    reasoning_content)。若 reasoning_content 未回填, 真实端点在此返回 400,
    用例即失败 —— 这是该缺陷的唯一防线。
    """
    model = make_model()
    loop = AgentLoop(model=model, tools=[LOOKUP_TOOL], guard=LoopGuard(max_turns=5))
    try:
        result = await loop.run([{"role": "user", "content": USER_PROMPT}])
    finally:
        await model.aclose()

    # 用例有效性前置: turn 1 必须真的产生 reasoning, 否则本用例无法暴露缺陷.
    # 上游偶尔不吐思维链 (2026-09-13 实测连跑三次: 过 / 挂 / 过), 这时本用例测不出
    # 想测的东西 —— 跳过并说明原因, 而不是红着 (红会被误读成「框架坏了」)
    if not result.turns[0].response.reasoning:
        pytest.skip("本次上游未产出思维链, 用例失去防线意义")
    # 前置成立后, 走到第二轮即证明 turn 2 请求被端点接受 (缺字段此处已 400)
    assert result.turn_count >= 2, "未发生第二轮决策, 工具路径未被走到"
    assert result.outcome is LoopOutcome.FINISHED

    # 工具确实执行并回填 (wire 结构完整)
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "20260701123456" in tool_messages[0]["content"]
    assert "已发货" in tool_messages[0]["content"]

    # reasoning_content 确实回填进 wire 历史 (而非被 agent 层丢弃) ——
    # 断言 turn 1 那条带 tool_calls 的 assistant 消息, 它正是 turn 2 请求
    # 的一部分 (终止轮是否产生 reasoning 由模型自由决定, 不作断言)
    tool_call_msg = next(m for m in result.messages if m.get("tool_calls"))
    assert tool_call_msg.get("reasoning_content")


async def test_max_tokens_forces_length_truncation() -> None:
    """max_tokens 透传生效: 小上限强制 finish_reason=length 并走续写路径.

    unit 层覆盖不到 —— ScriptedModel 产不出真实的 length 截断, 所以截断
    处理链路此前从未在真实端点上跑过。
    """
    model = chat_model_from_env()
    loop = AgentLoop(
        model=model,
        thinking=False,  # 思维链与正文共享输出配额, 本用例只验证 max_tokens 生效
        max_tokens=128,
        # 预算要够写完: max_tokens=128 约合 100 汉字/片, 500 字长文需 5~6 片,
        # 原来配 3 会在写完之前耗尽预算 (outcome=TRUNCATION_LIMIT, content 为空),
        # 于是用例时红时绿 (2026-09-13 实测). 这里放宽到 10, 只保留「有界」这一点
        max_truncations=10,
    )
    try:
        result = await loop.run(
            [{"role": "user", "content": "请写一篇 500 字左右介绍长江的短文"}]
        )
    finally:
        await model.aclose()

    # 首轮被真实截断 (max_tokens=128 远小于长文所需)
    assert result.turns[0].response.finish_reason is FinishReason.LENGTH
    assert result.truncation_count >= 1
    # 截断后走 CONTINUE 续写, 最终拼合出正文 (预算耗尽即拿不到正文, 上一行注释)
    assert result.content
