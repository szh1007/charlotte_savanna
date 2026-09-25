"""EventBus: 流式事件状态机 + 分发 (#4, 事件总线).

一句话理解: agent loop 一边跑一边「报账」(emit), EventBus 负责三件事 ——
给事件编上序号 (seq), 检查事件顺序是否合法 (状态机), 把事件交给出口
(sink, 推给前端) 与扩展点 (on_event hook, P2 观测).

大白话版:
- 现实问题: 用户发一句话, agent 背后要忙十几秒到几分钟 (问模型 → 跑工具 →
  再问模型 ...). 这期间屏幕上什么都没有, 用户以为卡死了 —— 得有人边干边
  「喊话」, 前端才能实时显示进度.
- 打个比方: 这是直播现场. agent 每干一件事就喊一嗓子 (emit), 本文件是现场的
  「场记 + 纪律委员 + 大喇叭」—— 场记给每声喊话编号 (seq); 纪律委员检查喊话
  顺序不许乱; 大喇叭把话同时传给门外观众 (sink = 前端) 与台下观察员
  (on_event hook = 日志 / 监控插件).
- 编号有什么用: 断线重连时前端说「我听到第 5 声了, 从第 6 声接着给我」,
  不用重听一遍 (seq 即 after_event_id).
- 本文件: 这套「编号 → 查顺序 → 分发」的实现, 入口是 EventBus.emit()
  (顺序为什么不能乱, 见下一段).

为什么要状态机: 事件流是前端渲染的唯一依据, 乱序等于把 bug 直接画到用户
屏幕上 —— tool_result 先于 tool_call 会让界面出现「凭空回来的工具结果」,
final 之后又来事件会让界面在给出答案后继续跳动. 这些顺序约束与 wire 历史
的配对约束 (#10: tool 消息必须紧跟带 tool_calls 的 assistant) 是同一条规则
在两条通道上的体现; 在这里拦下, 属于框架自检 (违反即 EventSequenceError).

四条不变量 (事件流契约):
1. seq 每 run 从 1 起单调递增
2. tool_result 必须匹配一条未闭合的 tool_call (按 tool_call_id 配对);
   同一 id 在**闭合前**不得重复开启 —— 注意只约束未闭合期间: 真实上游的
   tool_call_id 是逐响应重置的 (如 `call_0` 每轮重来), 跨轮复用同一 id 属
   正常现象, 不是乱序
3. 仍有未闭合 tool_call 时不得发终局事件 (工具结果必须回填完);
   **只有 `approval_required` 是例外** —— 它说的正是「有一条调用**故意**没回填」
   (那条调用在等人批), 拿「工具没回填完」去拦它, 等于把「挂起」误判成乱序
4. final / error / approval_required 是终局: 之后不得再发任何事件 (含第二个终局)

reasoning 是**旁路通道** (#11): 思维链增量随时可发 (它不参与主序列, 也不
改变状态), 但终局之后同样不再出现. context_compacted (#7) 同理: 它描述
「这一轮的输入被压过」, 与工具配对无关, 同样受「终局之后不得再发」约束.
主序列即:

    [thinking] → (tool_call → tool_result)+ → [thinking] → ... → final | error
                                                              | approval_required

分发顺序: sink 先, on_event hook 随后 —— 事件流出口优先 (前端不被插件拖慢),
插件异常由 hook 注册表隔离留痕 (见 hooks/registry.py), sink 异常则向上传播
(传输通道坏了必须让调用方知道).
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from CharAgent.hooks.registry import HookRegistry
from CharAgent.hooks.utils.types import HookPoint
from CharAgent.stream.utils.errors import EventSequenceError
from CharAgent.stream.utils.types import (
    TERMINAL_TYPES,
    EventSink,
    EventType,
    StreamEvent,
)


def _require_call_id(data: Mapping[str, Any]) -> str:
    """取出并校验 tool_call_id (工具事件与结果靠它配对)."""
    call_id = data.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        raise EventSequenceError(
            f"工具类事件缺 tool_call_id (非空字符串): {dict(data)!r}"
        )
    return call_id


class EventBus:
    """一次 run 的事件总线: 编号 → 状态机校验 → 分发.

    生命周期与 run 绑定 (AgentLoop.run 内部 new 一个): seq 每 run 从 1 重来,
    状态机状态 (未闭合工具 / 是否已终局) 也逐 run 独立 —— 与 turns / tokens
    的逐 run 独立语义一致 (run 级状态隔离).

    attributes:
        (无公开属性; 状态经 seq / closed 只读查询)
    """

    def __init__(
        self,
        *,
        sink: EventSink | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        """
        Args:
            sink: 事件出口回调 (同步或异步), None 表示不接出口 (CLI 不打印
                事件 / 测试不收集); 异常向上传播.
            hooks: hook 注册表, 用于触发 HookPoint.ON_EVENT (每个事件分发后);
                None 表示无扩展点 (内部用空注册表, fire 立即返回).
        """
        self._sink = sink
        self._hooks = hooks if hooks is not None else HookRegistry()
        self._seq = 0
        # 未闭合的工具调用 id 集合 (工具结果与终局判定的依据)
        self._open: set[str] = set()
        self._closed = False

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------

    @property
    def seq(self) -> int:
        """已产出的事件数 (下一个事件序号为 seq + 1)."""
        return self._seq

    # ------------------------------------------------------------------
    # 产出
    # ------------------------------------------------------------------

    async def emit(self, event_type: EventType, **data: Any) -> StreamEvent:
        """产出一个事件: 状态机校验 → 编号 → 建事件 → 分发 (sink → hook).

        Args:
            event_type: 事件类型. 传字符串会归一为 EventType —— 保证
                StreamEvent.type 恒为枚举 (否则 to_dict 的 .value 与状态机
                的枚举比较都会退化).
            **data: 业务载荷, 会原样进入 StreamEvent.data.

        Returns:
            StreamEvent: 已编号并分发完毕的事件 (调用方可直接取用).

        Raises:
            EventSequenceError: 违反四条不变量 (见模块 docstring) —— 框架
                接线错误, 在事件产出的瞬间暴露, 而不是把乱序流推给前端.
        """
        event_type = EventType(event_type)
        self._advance(event_type, data)
        self._seq += 1
        event = StreamEvent(type=event_type, seq=self._seq, data=data)
        if self._sink is not None:
            result = self._sink(event)
            if inspect.isawaitable(result):
                await result
        await self._hooks.fire(HookPoint.ON_EVENT, event=event)
        return event

    def _advance(self, event_type: EventType, data: Mapping[str, Any]) -> None:
        """状态机: 校验本次事件合法并推进状态 (不合法则先抛错, 状态不变)."""
        if self._closed:
            raise EventSequenceError(
                f"事件流已是终局 (final/error), 之后不得再发事件: {event_type.value}"
            )
        if event_type is EventType.TOOL_CALL:
            call_id = _require_call_id(data)
            if call_id in self._open:
                raise EventSequenceError(
                    f"tool_call_id 重复: {call_id!r} —— 该 id 尚未闭合 (结果未回填),"
                    " 同一批次内不得重复开启"
                )
            self._open.add(call_id)
        elif event_type is EventType.TOOL_RESULT:
            call_id = _require_call_id(data)
            if call_id not in self._open:
                raise EventSequenceError(
                    f"tool_result 找不到匹配的未闭合 tool_call: {call_id!r}"
                    " (结果必须先有对应的调用)"
                )
            self._open.discard(call_id)
        elif event_type in TERMINAL_TYPES:
            if self._open and event_type is not EventType.APPROVAL_REQUIRED:
                # 这一条防的是「说跑完了, 可工具结果还没回填」—— 前端会以为那一轮
                # 完了. 挂起恰恰相反: 那条欠着的调用**就是**在等人批, 事件把它
                # 明说了, 所以放它过去 (未闭合的 id 留在集合里, 反正下一步就 _closed)
                raise EventSequenceError(
                    f"仍有 {len(self._open)} 个工具调用未回填结果, 不能发终局事件:"
                    f" {sorted(self._open)}"
                )
            self._closed = True
        # thinking / reasoning / context_compacted: 无状态约束 (旁路或叙述类,
        # 不参与工具配对; 终局之后不得再发这条约束由上面那道 _closed 判定管)
