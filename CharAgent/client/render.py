"""终端渲染: 把事件流与运行结果画成人能看懂的文本.

一句话理解: 事件流 (stream 包产出的六类事件) 是**给机器读**的结构化数据 ——
类型 + 编号 + 载荷; 本文件负责把它翻成终端上一行行文字, 就像前端把同一份事件流
渲染成聊天气泡. 差别只在画布: 那边是浏览器, 这边是终端.

与 hooks / SSE 的关系 (三条通道, 同一条事件流):
- `EventPrinter` 走的是 **EventSink 出口** (与 P1 server 推 SSE 完全同一个协议):
  `AgentLoop(event_sink=printer)` 就能跑 —— 出口是同步回调, 收下事件立刻打印,
  不缓冲不排队. 事件本身要可靠送达 (出口异常向上抛), 与 hook 的「插件异常隔离」
  是两条不同的通道 (见 stream/bus.py).
- 本文件**不产生**事件, 也不改事件: 只读不改, 与测试里的 EventCollector 同型.

一条容易踩的约定: **final 事件只印一行摘要, 正文由调用方从
`LoopResult.content` 打印**. 理由: delta 只作渐进预览、
`LoopResult.content` 才是权威值 (CONTINUE 截断续写时它是跨段拼合结果, 与
消息历史里最后一条 assistant 的 content 不是一回事). 两处都印会重复一遍答案.

大白话版: 这是「终端上的那块屏幕」. agent 每干一件事就喊一嗓子, 本文件把那声
喊话翻译成一行带前缀的字 —— 开始想了、要调哪个工具、工具回了什么、最后答了
什么. 用户于是能边跑边看, 而不是对着空屏等一分钟.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from CharAgent.agent.utils.types import LoopOutcome, LoopResult
from CharAgent.stream.utils.types import EventType, StreamEvent

# ANSI 颜色码 (只在 color=True 时拼接; 关掉时文本与不加色完全一样).
# 只列用到的: 加粗与黄色没有用武之地 (六类事件各占一色, 再分就是噪音)
_RESET = "\033[0m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"

# reasoning 事件单条在终端上最多打多少个字. 终端没有「折叠」这个交互, 思维链
# 一次能吐几千字, 全打会把答案冲出屏幕 —— 只打头一段并注明总长度 (想看全文的
# 场合是 P1 的会话接口与 P2 的 trace, 不是这一行)
REASONING_LIMIT = 200

# 事件行的前缀 (方括号标签: 纯 ASCII, 重定向进文件后也一眼能 grep)
_TAGS: dict[EventType, str] = {
    EventType.THINKING: "thinking",
    EventType.TOOL_CALL: "tool_call",
    EventType.TOOL_RESULT: "tool_result",
    EventType.REASONING: "reasoning",
    EventType.FINAL: "final",
    EventType.ERROR: "error",
}

# 每类事件的颜色 (thinking / reasoning 是过程信息, 调暗; 工具与结论分别用青 / 绿;
# 出错用红)
_COLORS: dict[EventType, str] = {
    EventType.THINKING: _DIM,
    EventType.REASONING: _DIM,
    EventType.TOOL_CALL: _CYAN,
    EventType.TOOL_RESULT: _GREEN,
    EventType.FINAL: _GREEN,
    EventType.ERROR: _RED,
}


def paint(text: str, color: str, *, enabled: bool) -> str:
    """给一段文本上色 (enabled=False 时原样返回, 一个转义符都不加)."""
    if not enabled:
        return text
    return f"{color}{text}{_RESET}"


def format_arguments(arguments: str) -> str:
    """工具参数原文 (JSON 字符串) -> 一行好看的展示文本.

    事件载荷里的 `arguments` 是模型给的**原始 JSON 字符串** (框架刻意不预解析,
    #10 —— 畸形 JSON 正是自纠错路径的信号). 展示层顺手解析一下只是为了让
    `{"order_no": "2026..."}` 别带着转义引号显示; 解析失败就原样打印并标注
    「畸形 JSON」—— 那本身就是要给用户看的信息 (模型填错了参数, 工具会给
    可操作错误, 下一轮自纠错).
    """
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return f"{arguments} (畸形 JSON)"
    if isinstance(parsed, dict):
        return json.dumps(parsed, ensure_ascii=False)
    return arguments


def _format_ms(value: float) -> str:
    """毫秒数 -> 给人看的短文本 (小于 10ms 保留一位小数, 其余取整)."""
    return f"{value:.1f}ms" if value < 10 else f"{value:.0f}ms"


class EventPrinter:
    """事件出口 (EventSink 协议): 每来一个事件就往终端画一行.

    注入方式与 P1 的 SSE 出口、测试里的收集器完全一样 —— 它就是一个同步回调:
    `AgentLoop(model=..., event_sink=EventPrinter())`. 六类事件各有各的版式,
    见 `format_event`.

    Args:
        writer: 输出函数 (默认 print; 测试传一个收集器, 或传文件对象的 write).
        color: 是否上 ANSI 颜色 (默认 True; 非 TTY / --plain 时由调用方关掉).
    """

    def __init__(
        self,
        writer: Callable[[str], Any] | None = None,
        *,
        color: bool = True,
    ) -> None:
        self._writer = print if writer is None else writer
        self._color = color

    def __call__(self, event: StreamEvent) -> None:
        """EventSink 协议实现 (同步): 把事件画成一行文字."""
        self._writer(self.format_event(event))

    def _tag(self, event_type: EventType) -> str:
        """带颜色的方括号标签 (如 `[tool_call]`).

        未知事件类型不炸: 标签用类型取值原文 (``.get`` 的兜底), 颜色调暗 ——
        以后新增 approval_required 这类事件时, 若本文件还没跟上, 用户看到的
        是一行「[approval_required] {...}」而不是一个 KeyError.
        """
        text = f"[{_TAGS.get(event_type, str(event_type))}]"
        return paint(text, _COLORS.get(event_type, _DIM), enabled=self._color)

    def format_event(self, event: StreamEvent) -> str:
        """一个事件 -> 一行文本 (不直接输出, 好单测).

        分派用 match 而不是查表: 六类事件的版式各有各的取值, 摆在这里一眼能
        对着各自的载荷逐条核. 新增事件类型 (如 approval_required) 时忘了加
        分支会走 `case _`, 原样吐出而不是静默丢掉.
        """
        data = event.data
        match event.type:
            case EventType.THINKING:
                body = self._line_thinking(data)
            case EventType.TOOL_CALL:
                body = self._line_tool_call(data)
            case EventType.TOOL_RESULT:
                body = self._line_tool_result(data)
            case EventType.REASONING:
                body = self._line_reasoning(data)
            case EventType.FINAL:
                body = self._line_final(data)
            case EventType.ERROR:
                body = self._line_error(data)
            case _:  # 防御分支: 新增事件类型时先原样吐出, 不静默丢
                body = str(data)
        return f"{self._tag(event.type)} {body}"

    # ------------------------------------------------------------------
    # 各类事件的版式 (数据来自 agent/utils/events.py 的载荷构造)
    # ------------------------------------------------------------------

    def _line_thinking(self, data: dict[str, Any]) -> str:
        """thinking: 非终止轮的助手正文 (过程叙述, 如「我先查一下订单」)."""
        return str(data.get("message") or "")

    def _line_tool_call(self, data: dict[str, Any]) -> str:
        """tool_call: 工具名 + 参数 (原始 JSON 解析成紧凑一行, 见 format_arguments)."""
        name = data.get("tool_name")
        return f"{name}({format_arguments(str(data.get('arguments', '')))})"

    def _line_tool_result(self, data: dict[str, Any]) -> str:
        """tool_result: 成功给摘要与耗时; 失败给可操作错误 (#2).

        失败那行额外上红色 —— 这是用户最需要看见的一行: 它说明「模型填错了什么、
        下一轮会怎么改」, 而不是一句「工具失败」.
        """
        name = data.get("tool_name")
        spent = _format_ms(float(data.get("duration_ms") or 0.0))
        if data.get("status") == "ok":
            return f"{name} ok ({spent}): {data.get('summary') or ''}"
        return paint(
            f"{name} 失败 ({spent}): {data.get('error')}",
            _RED,
            enabled=self._color,
        )

    def _line_reasoning(self, data: dict[str, Any]) -> str:
        """reasoning: 思维链增量 (旁路通道, 不混进正文; 超长只打头一段)."""
        delta = str(data.get("delta") or "")
        if len(delta) <= REASONING_LIMIT:
            return delta
        head = delta[:REASONING_LIMIT]
        return f"{head}... (共 {len(delta)} 字, 终端只打前 {REASONING_LIMIT} 字)"

    def _line_final(self, data: dict[str, Any]) -> str:
        """final: 只报「怎么结束的」, 正文由调用方从 LoopResult.content 打印.

        同理见模块 docstring: content 的权威值在 LoopResult 上 (截断续写时是
        跨段拼合结果), 这里再印一遍会重复.
        """
        reason = data.get("finish_reason") or "-"
        tokens = data.get("tokens") or 0
        elapsed = float(data.get("elapsed_ms") or 0.0) / 1000.0
        return f"答复就绪 (finish_reason={reason}, {tokens} tokens, {elapsed:.1f}s)"

    def _line_error(self, data: dict[str, Any]) -> str:
        """error: 异常结束的唯一终局事件 (code + 事实性说明)."""
        error = data.get("error") or {}
        return paint(
            f"{error.get('code')}: {error.get('message')}",
            _RED,
            enabled=self._color,
        )


def format_outcome(outcome: LoopOutcome) -> str:
    """结束原因 -> 中文短语 (终端给读者看, 不直接抛枚举值).

    枚举值仍然照原样出现在事件流里 (那是跨版本稳定契约); 这一层只是翻译.
    """
    return _OUTCOME_TEXT.get(outcome, outcome.value)


_OUTCOME_TEXT: dict[LoopOutcome, str] = {
    LoopOutcome.FINISHED: "答完了",
    LoopOutcome.MAX_TURNS: "达到轮数上限, 被迫停下",
    LoopOutcome.TOKEN_BUDGET: "达到 token 预算上限, 被迫停下",
    LoopOutcome.TIME_LIMIT: "达到时间上限, 被迫停下",
    LoopOutcome.TRUNCATION_LIMIT: "反复截断, 未能在重试上限内写完",
    LoopOutcome.SERVER_INTERRUPTED: "上游中断了本次生成",
}


def format_result(result: LoopResult) -> str:
    """一次 run 的 LoopResult -> 一行摘要 (轮数 / token / 耗时 / 结束原因).

    这一行是「这次跑成什么样」的账目: 轮数看 loop 转了几圈, token 看花了多少,
    耗时看值不值得等, 结束原因决定下一步该干什么 (答完了 / 被刹车 / 该续跑).
    """
    seconds = result.elapsed_ms / 1000.0
    return (
        f"{format_outcome(result.outcome)} · "
        f"{result.turn_count} 轮 · {result.total_tokens} tokens · "
        f"{seconds:.1f}s"
    )


def format_answer(content: str | None) -> str:
    """最终答复 -> 终端要打印的文本 (无正文时给一句实话, 不印空行).

    无正文不是错误: 纯工具调用收尾、guard 刹车、上游中断都可能没有正文 ——
    此时事件流里的 error 行已经说清了原因, 这里只补一句「没有正文」免得
    用户以为程序卡住了.
    """
    if content is None or not content.strip():
        return "(本次没有产出正文 —— 原因见上面的 error 行)"
    return content
