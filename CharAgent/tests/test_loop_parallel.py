"""并行工具执行测试 (difficulties #1 #2): 并发 + 部分失败.

场景 → 断言:
- 并发执行: 同一 assistant 消息的两个 tool_call 同时处于执行中 (门控同步
  「串行实现下第二个工具永不进入」, 严格证明非串行 —— 时间戳验证的确定性版)
- 回填保序: tool 消息按 tool_calls 原顺序整体回填, 保持并行语义 (#1)
- 部分失败: 成功结果与失败原因一起回填, 失败不拖垮成功 (return_exceptions)
- 意外异常隔离: traceback 不外泄 (通用内部文案), 其他工具正常执行
- 未知工具: 幻觉工具名回填可操作错误并列出可用工具 (#2)

载体工具就地定义 (Seam 3); 模型为 ScriptedModel (Seam 1).
"""

from __future__ import annotations

import asyncio

import pytest
from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, LoopOutcome
from CharAgent.tool import Tool, ToolActionableError, tool

USER_MSG = {"role": "user", "content": "同时处理"}


def _echo(
    message: str,
) -> str:
    """成功工具载体 (同步)."""
    return f"echo:{message}"


async def _slow_echo(
    message: str,
) -> str:
    """慢工具载体: 异步 + 短暂延时, 同批中完成得最晚 (验证回填保序)."""
    await asyncio.sleep(0.05)
    return f"slow:{message}"


def _failing() -> str:
    """可操作错误工具载体 (#2): 作者主动 raise, 消息面向模型."""

    raise ToolActionableError("order_no 应为 14 位数字, 实际 'abc123', 请核对后重试")


def _boom() -> str:
    """意外异常工具载体: 非业务规则的代码故障."""

    raise RuntimeError("boom: 底层服务连接失败")


def _make_gated_tool(
    name: str,
    entered: asyncio.Queue[str],
    gate: asyncio.Event,
) -> Tool:
    """门控工具: 进入执行即报告, 然后等待放行.

    「并发证据」同步桩 —— 若 runtime 串行 await 第一个工具, 它永远卡在
    gate, 第二个工具不会进入; 并发实现则两个工具都会进入等待.
    """

    @tool(name=name)
    async def gated() -> str:
        await entered.put(name)
        await gate.wait()
        return f"{name}:done"

    return gated


# ---------------------------------------------------------------------------
# 并发执行 (验收 #1: 验证非串行)
# ---------------------------------------------------------------------------


async def test_multiple_tool_calls_run_concurrently() -> None:
    """两个工具同时处于执行中 → 证明并发而非串行等待."""
    entered: asyncio.Queue[str] = asyncio.Queue()
    gate = asyncio.Event()
    tool_a = _make_gated_tool("tool_a", entered, gate)
    tool_b = _make_gated_tool("tool_b", entered, gate)
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("tool_a"),
                make_tool_call("tool_b"),
            ),
            text_response("两个工具都完成了"),
        ]
    )
    loop = AgentLoop(model=model, tools=[tool_a, tool_b])
    task = asyncio.create_task(loop.run([dict(USER_MSG)]))
    try:
        got: list[str] = []
        for _ in range(2):
            # 串行实现下: 第一个工具卡在 gate, 第二个永不进入 → wait_for 超时
            got.append(await asyncio.wait_for(entered.get(), timeout=1))
    except TimeoutError:
        gate.set()  # 释放卡住的任务再取消, 避免泄漏后台 task
        task.cancel()
        pytest.fail("两个工具未同时进入执行 —— loop 在串行执行并行工具")
    gate.set()
    result = await task

    assert set(got) == {"tool_a", "tool_b"}  # 都在放行前已开始执行
    assert result.outcome is LoopOutcome.FINISHED
    assert result.content == "两个工具都完成了"


async def test_parallel_results_backfilled_in_call_order() -> None:
    """回填按 tool_calls 原顺序而非完成顺序 (#1: 并行语义稳定可重放).

    slow_echo (0.05s) 排在 echo 之后: 并发下 echo 先完成, 但回填必须仍按
    call_1 → call_2 的原顺序 —— 若按完成顺序回填, 历史顺序会随机抖动.
    """
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}', call_id="call_1"),
                make_tool_call("slow_echo", '{"message": "hi"}', call_id="call_2"),
            ),
            text_response("两次回声都收到"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[tool(_echo, name="echo"), tool(_slow_echo, name="slow_echo")],
    )
    result = await loop.run([dict(USER_MSG)])

    roles = [m["role"] for m in result.messages]
    assert roles == ["user", "assistant", "tool", "tool", "assistant"]
    assert [m["tool_call_id"] for m in result.messages[2:4]] == ["call_1", "call_2"]
    assert result.messages[2]["content"] == "echo:hi"
    assert result.messages[3]["content"] == "slow:hi"


# ---------------------------------------------------------------------------
# 部分失败 / 意外异常 / 未知工具
# ---------------------------------------------------------------------------


async def test_partial_failure_backfills_success_and_error() -> None:
    """同轮一成一败: 成功结果与可操作错误一起回填, 失败不拖垮成功 (#1/#2)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "ok"}', call_id="call_ok"),
                make_tool_call("failing", "{}", call_id="call_bad"),
            ),
            text_response("我知道了, 让用户核对订单号"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[tool(_echo, name="echo"), tool(_failing, name="failing")],
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    tool_messages = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_messages) == 2
    # 成功消息原文回填; 失败消息是可操作错误 (含期望与实例), 供模型自纠错
    assert tool_messages[0]["content"] == "echo:ok"
    assert "order_no 应为 14 位数字" in tool_messages[1]["content"]
    assert "abc123" in tool_messages[1]["content"]
    # 模型第二轮同时看到成功结果与失败原因 (部分失败语义 #1)
    assert model.calls[1]["messages"][-2:] == tool_messages


async def test_unexpected_exception_isolated_and_sanitized() -> None:
    """意外异常: 其他工具正常完成, 失败回填通用文案不泄 traceback (#2)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("boom", "{}", call_id="call_bad"),
                make_tool_call("echo", '{"message": "ok"}', call_id="call_ok"),
            ),
            text_response("完成"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[tool(_boom, name="boom"), tool(_echo, name="echo")],
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    failed = result.messages[2]["content"]  # tool_calls 顺序: boom 在前
    assert "内部错误" in failed
    assert "boom" not in failed  # 根因不外泄给模型 (executor 保留于 exception)
    assert result.messages[3]["content"] == "echo:ok"  # 成功工具不受影响


async def test_unknown_tool_name_backfilled_actionable() -> None:
    """模型幻觉工具名: 回填可操作错误 (含可用工具列表), loop 不崩溃 (#2)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("ghost_tool", '{"x": 1}')),
            text_response("抱歉, 我没有这个工具"),
        ]
    )
    loop = AgentLoop(model=model, tools=[tool(_echo, name="echo")])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    backfilled = result.messages[2]["content"]
    assert "ghost_tool" in backfilled
    assert "echo" in backfilled  # 列出可用工具引导模型改选
    assert result.content == "抱歉, 我没有这个工具"


async def test_mixed_known_and_unknown_tools_in_one_message() -> None:
    """同一 assistant 消息里已知 + 幻觉工具: 同批处理, 各自结果独立回填.

    真实端点上模型常一次给出多个 tool_call, 个别名字幻觉 —— 已知的必须
    照常执行 (不被未知的拖累), 未知的回填可操作错误 (#1 + #2 交叉).
    """
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}', call_id="call_ok"),
                make_tool_call("ghost", '{"x": 1}', call_id="call_ghost"),
            ),
            text_response("处理完毕"),
        ]
    )
    loop = AgentLoop(model=model, tools=[tool(_echo, name="echo")])
    result = await loop.run([dict(USER_MSG)])

    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    # 保序: 与 tool_calls 声明顺序一致, 且各自配对正确的 tool_call_id
    assert tool_msgs[0]["tool_call_id"] == "call_ok"
    assert tool_msgs[0]["content"] == "echo:hi"  # 已知工具正常执行
    assert tool_msgs[1]["tool_call_id"] == "call_ghost"
    assert "ghost" in tool_msgs[1]["content"]  # 未知工具回填可操作错误
    assert "echo" in tool_msgs[1]["content"]  # 并列出可用工具
    assert result.content == "处理完毕"
