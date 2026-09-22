"""事件载荷构造与终局语义 (loop.py 拆出的纯函数/常量).

大白话版 (本文件 = 主循环的「话术本」):
- 分工: 主循环只管「什么时候喊」, 本文件管「喊什么措辞」—— 改文案不用碰
  循环逻辑.
- 三种措辞: 工具调用怎么念 (工具名 + 参数原文 + 轮次); 工具结果怎么念 (成功
  给摘要, 失败给「改一下就能用」的错误提示); 结束时报什么 (正常 → final
  「答完了」; 刹车 / 上游中断 / 被安全策略拦截 → error「出问题了」).
- 一条硬规矩: 结束事件绝不撒谎 —— 机器人没给出答案时不许喊「答完了」.
  真话是「被刹车拦下了」, 道歉话术 (「请稍后再试」) 是产品文案, 归服务层,
  框架只陈述事实.
- 顺带保证一件事: 结束事件与返回值同源 (都从 LoopResult 读), 不会出现
  「返回给调用方的结果」和「推给前端的事件」各说各话.

对齐 tool/utils/messages.py 与 agent/utils/messages.py 的拆分动机 —— 行为
(AgentLoop 的 while 循环) 与「事件流里每个字段长什么样 / run 结束时发什么」
分离:
- tool_call_data / tool_result_data: 工具调用与结果 → 事件载荷 (参数保真为
  原始 JSON 字符串不预解析 #10; 成功结果只带截断摘要, 全文不进事件流)
- context_compacted_data: 一次上下文压缩 → 事件载荷 (#7; 数值由策略算好,
  这里只搬运)
- emit_terminal: run 的**单一终局出口** —— 从 LoopResult 派生恰好一个终局
  事件 (正常结束 final / 异常结束 error), 避免调用方各写一份判定
- TERMINAL_ERROR_TEXT / terminal_error_code: 异常结束的 error 事件契约 ——
  **走 error 而非 final** (没有答复的结束不该有终局答复事件), code 取
  LoopOutcome 值; 文案只陈述事实, 用户可见的降级话术归服务层
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from CharAgent.agent.utils.types import LoopOutcome, LoopResult
from CharAgent.model.utils.types import (
    FinishReason,
    ModelToolCall,
)
from CharAgent.stream.bus import EventBus
from CharAgent.stream.utils.types import (
    TOOL_RESULT_SUMMARY_LIMIT,
    EventType,
)
from CharAgent.tool import ToolExecution

if TYPE_CHECKING:  # 只为类型标注: 运行时 import 会把 agent 与 compaction 绕成环
    from CharAgent.agent.compaction import CompiledView

# 终局 error 事件的 code → 事实性说明 (非用户话术; 降级文案归服务层).
# code 取 LoopOutcome 值, 另加 content_filter —— 它在 loop 语义上属 FINISHED
# (拦截语义归调用方), 但既然输出被安全策略拦下, 事件层就不该把它当
# 可展示答复推给前端.
TERMINAL_ERROR_TEXT: dict[str, str] = {
    LoopOutcome.MAX_TURNS.value: "已达最大轮数限制, 未能产出最终答复",
    LoopOutcome.TOKEN_BUDGET.value: "已达本次运行的 token 预算上限, 未能产出最终答复",
    LoopOutcome.TIME_LIMIT.value: "已达本次运行的时间上限, 未能产出最终答复",
    LoopOutcome.TRUNCATION_LIMIT.value: "输出反复被截断且未能在重试上限内完成",
    LoopOutcome.SERVER_INTERRUPTED.value: "上游中断了本次生成, 未能产出完整答复",
    "content_filter": "输出被安全策略拦截, 不作可展示答复",
}


def summarize(text: str) -> str:
    """工具结果 → 事件流摘要 (超长截断 + 省略号, 事件流轻量化; 全文仍在 wire 历史)."""
    if len(text) <= TOOL_RESULT_SUMMARY_LIMIT:
        return text
    return text[:TOOL_RESULT_SUMMARY_LIMIT] + "..."


def tool_call_data(call: ModelToolCall, *, turn: int) -> dict[str, Any]:
    """模型发起的一次工具调用 → tool_call 事件载荷.

    arguments 原样透出 (JSON 字符串): 框架不替消费方解析 —— 畸形 JSON 正是
    自纠错路径的信号 (#2), 由工具执行层给出可操作错误.
    """
    return {
        "tool_call_id": call.id,
        "tool_name": call.name,
        "arguments": call.arguments,
        "status": "started",
        "turn": turn,
    }


def tool_result_data(
    call: ModelToolCall, execution: ToolExecution, *, turn: int
) -> dict[str, Any]:
    """一次工具执行 → tool_result 事件载荷 (成功带摘要 / 失败带可操作错误)."""
    data: dict[str, Any] = {
        "tool_call_id": call.id,
        "tool_name": call.name,
        "status": "ok" if execution.ok else "error",
        "duration_ms": round(execution.duration_ms, 3),
        "turn": turn,
    }
    if execution.ok:
        data["summary"] = summarize(execution.content)
    else:
        data["error"] = execution.error or "工具执行失败"
    return data


def context_compacted_data(compiled: CompiledView, *, turn: int) -> dict[str, Any]:
    """一次上下文压缩 → context_compacted 事件载荷 (#7).

    「压了什么 / 省了多少」由策略自己算 (见 CompiledView) —— 事件层只搬运,
    不重新猜一遍: 两处各算一次迟早会各说各话.

    两个 token 数**不是同一把尺子**, 别相加: `estimated_tokens` 是「这份视图
    作为一次请求大概多大」(锚在上游给的 input_tokens 上, 含工具 schema 这类
    固定开销), `saved_tokens` 是账本与视图用**同一个字符启发式**量出来的差
    (相对量; 压后反而更大时为负). 两者都与上游账单不是同一个数 —— 要精确的
    输入 token 只有上游的 usage.

    warning 恒在 (正常时是 None): 字段恒定比「有时多一个键」好消费, 前端不必
    为它写两种分支.
    """
    return {
        "turn": turn,
        "dropped": compiled.dropped,
        "truncated": compiled.truncated,
        "estimated_tokens": compiled.estimated_tokens,
        "saved_tokens": compiled.saved_tokens,
        "summarized": compiled.summarized,
        "warning": compiled.warning,
    }


def terminal_error_code(
    outcome: LoopOutcome, finish_reason: FinishReason | None
) -> str | None:
    """终局 error 事件的 code; None 表示正常结束 (发 final).

    判据是「run 是怎么结束的」而非「有没有正文」:
    - 非 FINISHED (guard 刹车 / 截断超限 / 上游中断) → error, code 取
      LoopOutcome 值 —— 这些结束都没有可用的答复
    - FINISHED 但被安全策略拦截 (content_filter) → error —— 即便模型已吐出
      部分文本, 也不该作为可展示答复推给前端 (拦截语义归输出护栏)
    - FINISHED 且正常作答 → None (发 final); 模型给空文本也属正常结束,
      final.content 为 null 由前端按空答复处理
    """
    if outcome is not LoopOutcome.FINISHED:
        return outcome.value
    if finish_reason is FinishReason.CONTENT_FILTER:
        return "content_filter"
    return None


async def emit_terminal(bus: EventBus, result: LoopResult) -> None:
    """run 的单一终局出口: 从 LoopResult 派生**恰好一个**终局事件.

    正常结束 → final (content 为权威值, 前端收到即覆盖缓冲); 其余 → error
    (code + 事实性文案). 终局事件与 LoopResult 同源 (同一份 outcome / content /
    tokens / elapsed_ms), 保证 result 与事件流不会各说各话.
    """
    code = terminal_error_code(result.outcome, result.finish_reason)
    if code is None:
        await bus.emit(
            EventType.FINAL,
            content=result.content,
            finish_reason=(
                result.finish_reason.value if result.finish_reason else None
            ),
            outcome=result.outcome.value,
            tokens=result.total_tokens,
            elapsed_ms=round(result.elapsed_ms, 3),
        )
        return
    await bus.emit(
        EventType.ERROR,
        error={"code": code, "message": TERMINAL_ERROR_TEXT[code]},
    )
