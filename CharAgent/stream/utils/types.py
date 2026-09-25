"""stream 包静态零件: 事件类型与事件对象.

对齐 agent/utils/types.py 的组织惯例 —— 行为 (EventBus 的编号 / 状态机 /
分发) 在 stream/bus.py, 静态数据结构与契约常量集中于此供 bus / 门面 /
消费方 (P1 server SSE 层) 共享.

大白话版 (本文件 = 喊话规范表):
- 规定现场能喊哪 8 种话: 我在想 (thinking) / 我要去查 (tool_call) / 查回来了
  (tool_result) / 我的心理活动 (reasoning) / 我把旧对话压了一下
  (context_compacted) / **这一步要你本人确认** (approval_required) / 我答完了
  (final) / 我出问题了 (error).
- 规定每声喊话长什么样 (StreamEvent: 句式 + 编号 seq + 内容), 以及「话往哪儿
  递」的回调形状 (EventSink: 交给谁就由谁拿去显示).
- 顺带两个小约定: reasoning 是「心理活动」—— 前端折叠起来给人看, 绝不混进
  答案正文; 「终局」有四类 (final / error / approval_required), 一条流里恰好
  一个, 之后不再有任何事件.
- 为什么不在这里写逻辑: 表 (本文件) 与行为 (bus.py) 分开, 改契约不动实现.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """事件类型全集 (事件名即 SSE 的 event 字段).

    `approval_required` 是 HITL 挂起那一个 (#25): 它由 **loop 自己产出、参与状态
    机** —— 名字早在 P0 就留在这里了, 那时留的注释写着「由 loop 之外的审批模块
    产出, 不参与状态机」, 2026-09-25 (issue 34) 把这条落成了事实的反面: 挂起是
    运行自己走到的一个终点 (它要落帧、要等人、要从存档点续), 所以事件由 loop 发,
    并且是**终局**.
    """

    THINKING = "thinking"  # 非终止轮的助手正文 (过程叙述, 如「让我先查一下订单」)
    TOOL_CALL = "tool_call"  # 发起工具调用 (同一轮多条即并行)
    TOOL_RESULT = "tool_result"  # 工具返回 (status ok / error, 错误须可操作 #2)
    REASONING = "reasoning"  # 思维链增量 (旁路通道, 折叠展示 #11)
    CONTEXT_COMPACTED = "context_compacted"  # 这一轮的上下文被压缩过 (#7)
    APPROVAL_REQUIRED = "approval_required"  # 一次工具调用等人批 (#25): 流到此为止
    FINAL = "final"  # 最终答复 (content 为权威值)
    ERROR = "error"  # 异常终止 (code + message)


# 终局事件: 一条事件流里恰好一个, 之后不再有任何事件 (见 bus.EventBus).
#
# 三个成员各有各的「到此为止」: final = 这一轮跑完了; error = 这一轮没跑完就出
# 事了; approval_required = 这一轮**停在半路等人** (答复还没发生, 但这次 HTTP
# 请求确定结束了 —— 前端据此渲染确认卡, 用户给结论后再开一次 resume 的流).
TERMINAL_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.FINAL,
        EventType.ERROR,
        EventType.APPROVAL_REQUIRED,
    }
)

# tool_result.summary 保留的字符数上限: 事件流是给前端做渐进展示的, 不必携带
# 工具返回全文 (全文在 wire 历史与 P1 的消息接口里), 超长只截摘要并追加省略号
# (故摘要最长 201 字符; 事件流轻量化).
TOOL_RESULT_SUMMARY_LIMIT = 200


@dataclass(slots=True)
class StreamEvent:
    """一次运行产出的单个流式事件: 类型 + 序号 + 业务载荷 (#4).

    attributes:
        type: 事件类型 (EventType), 即 SSE 的 event 字段.
        seq: 事件序号, 每 run 从 1 起单调递增 —— 前端断点续拉按 after_event_id
            定位; P1 server 亦可将它映射为 SSE 的 id 字段.
        data: 业务载荷. 不含 run_id —— 框架层没有 run 概念, run_id 由 P1
            server 在转发时注入.
    """

    type: EventType
    seq: int
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """SSE data 直通形状: type + seq + 载荷平铺 (可直接 json.dumps)."""
        return {"type": self.type.value, "seq": self.seq, **self.data}


# 事件出口回调 (sink): 传输通道 (P1 server 推 asyncio.Queue / CLI 打印 / 测试
# 收集), 同步或异步都接受, 异常向上传播. 与 HookPoint.ON_EVENT 的分工: 前者
# 是事件流的出口 (传输必须可靠, 出错要让调用方看见), 后者是扩展点 (插件异常
# 被隔离留痕, 见 hooks/registry.py).
type EventSink = Callable[[StreamEvent], Awaitable[None] | None]
