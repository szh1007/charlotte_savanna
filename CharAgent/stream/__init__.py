"""stream 包: 流式事件总线与事件状态机 (issue 05, ADR-0007 扩展点之一).

设计依据 (CharAgent/docs):
- difficulties #4 (流式事件状态机) / #11 (reasoning 增量单独成事件)
- design/03-api.md §2 (SSE 事件协议, 事件字段清单与状态机契约的权威出处)
- ADR-0005 (流式传输用 SSE) / ADR-0007 (事件总线是 P2 模块的挂载点之一)

结构总览 (对齐 model / tool / agent 包惯例):
- bus.py       EventBus 行为主体: seq 编号 + 四条状态机不变量 + 分发
               (sink 出口优先, on_event hook 随后)
- utils/       支撑子包: EventType / StreamEvent / EventSink /
               TERMINAL_TYPES / TOOL_RESULT_SUMMARY_LIMIT (types)
               + StreamError / EventSequenceError (errors)

大白话版 (这个包 = 直播信号系统):
- 现实问题: 用户问一句话, agent 背后要忙十几秒到几分钟 (问模型 → 跑工具 →
  再问模型 ...). 用户盯着空屏幕会以为卡死 —— 需要边干边「喊话」, 让前端
  实时显示进度.
- 本包负责: 规定能喊哪几种话、给每句话编号 (断线重连从哪接着听)、检查喊话
  顺序不能乱、把话推给前端.
- 打个比方: bus.py 是「场记 + 纪律委员 + 大喇叭」, utils/ 是「喊话规范表」
  与「违规罚单」. 详见各文件开头的模块注释.

三条通道的区别 (容易混, 面试可讲):
- wire 历史: 发给模型的消息 (含 reasoning_content #11), 直通 /chat/completions
- 事件流 (本包): 推给前端的渐进展示, 前端渲染的唯一依据
- hook (hooks 包): 插件扩展点, 空注册零开销

用法 (消费事件流):

    from CharAgent.agent import AgentLoop
    from CharAgent.stream import EventBus, EventType

    events = []
    loop = AgentLoop(model=model, tools=[...], event_sink=events.append)
    await loop.run([{"role": "user", "content": "我的订单到哪了"}])
    # events: thinking → tool_call → tool_result → final

模块内部 import 走具体模块路径 (stream.bus, stream.utils.types), 不绕包门面;
对外公共 API 统一由本文件 __all__ 导出.
"""

from __future__ import annotations

from CharAgent.stream.bus import EventBus
from CharAgent.stream.utils.errors import EventSequenceError, StreamError
from CharAgent.stream.utils.types import (
    TERMINAL_TYPES,
    TOOL_RESULT_SUMMARY_LIMIT,
    EventSink,
    EventType,
    StreamEvent,
)

__all__ = [
    "TERMINAL_TYPES",
    "TOOL_RESULT_SUMMARY_LIMIT",
    "EventBus",
    "EventSequenceError",
    "EventSink",
    "EventType",
    "StreamError",
    "StreamEvent",
]
