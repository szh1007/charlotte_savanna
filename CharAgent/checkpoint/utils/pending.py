"""从消息历史里找出「欠模型结果」的工具调用 (挂起 / 断点恢复用, #5 / #25).

一句话理解: 正常的一轮里, 模型说「我要查订单 A 和 B」, 框架跑完就得把结果还
给模型 —— 就像老师收了作业, 批改完要发回去. 如果历史停在「模型要调工具」那
一步 (结果还没回填), 就说明这次运行是在半路停下的: 要么进程挂了, 要么在等人
批准. 恢复时要做的第一件事就是问一句: 现在还欠着哪几份「作业」?

为什么需要这么一个函数 (而不是几行简单循环): tool_call_id 会在不同轮次里重复
—— 上游每轮都从 `call_0` 重新编号 (同一说明见 stream/bus.py), 所以「按 id 建
字典、后面覆盖前面」的写法在这里是错的. 正确的读法只有一种: 顺着历史往后走,
只记住**最近一批**还没收到结果的调用 —— 碰到带 tool_calls 的 assistant 消息就
换一批, 碰到 tool 消息就从当前批里划掉一个. 走完剩下的, 就是欠着的.

返回空列表 = 历史是完整的 (该回填的都回填了), 正常跑完的 run 都是这样.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from CharAgent.model.utils.types import ModelMessage, ModelToolCall


def _read_call(raw: Any) -> ModelToolCall | None:
    """wire 里的一条 tool_call -> ModelToolCall; 形状不对就跳过 (返回 None).

    wire 的形状是嵌套的: {"id": ..., "type": "function", "function": {"name": ...,
    "arguments": ...}}. 这里的宽容是有意的 —— 历史可能由调用方手工拼出来 (比如
    测试里造一个「挂起点」), 一条坏数据不该让整段历史读不出来; 真正坏掉的调用
    也不值得去补做, 跳过即可.
    """
    if not isinstance(raw, dict):
        return None
    function = raw.get("function")
    if not isinstance(function, dict):
        return None
    call_id = raw.get("id")
    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(call_id, str) or not isinstance(name, str):
        return None
    return ModelToolCall(
        id=call_id,
        name=name,
        arguments=arguments if isinstance(arguments, str) else "",
    )


def pending_tool_calls(messages: Sequence[ModelMessage]) -> list[ModelToolCall]:
    """找出历史末尾「已请求、还没有结果」的工具调用 (顺序即模型给出的顺序).

    读法: 从前往后扫一遍, 手里始终只有「最近一批没拿到结果的调用」:
    - 遇到带 tool_calls 的 assistant 消息 -> 换一批 (上一批若有剩余, 说明历史
      本身不合法, 但那是上游该管的事; 这里只关心末尾欠着谁)
    - 遇到 tool 消息 -> 按 tool_call_id 从当前批里划掉一个
    - 其他消息 (user / 无工具调用的 assistant / system) -> 不动

    Args:
        messages: 完整 wire 消息历史 (快照里存的那份).

    Returns:
        list[ModelToolCall]: 还欠结果的调用; 历史完整时为空列表.
    """
    waiting: list[ModelToolCall] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            raw_calls = message["tool_calls"]
            if not isinstance(raw_calls, list):
                continue
            waiting = [call for call in map(_read_call, raw_calls) if call is not None]
        elif role == "tool":
            call_id = message.get("tool_call_id")
            waiting = [call for call in waiting if call.id != call_id]
    return waiting
