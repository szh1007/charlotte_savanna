"""agent 包共享数据结构与枚举: AgentLoop 的输入输出类型 (issue 04).

对齐 model/utils/types.py 的组织惯例 —— 行为 (AgentLoop.run 的循环语义)
在 agent/loop.py, 类型定义集中于此供 loop / guard / 门面共享引用.

- LoopOutcome: Loop 结束原因全集 (guard 触发点 + loop 分支放弃点)
- TruncationStrategy: length 截断的两种处理路径 (#10)
- TurnRecord: 每轮结束时的消息历史完整快照 (供 checkpoint 落盘)
- LoopResult: run 的完整结果 (历史 + 结束原因 + 每轮快照)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
)


class LoopOutcome(StrEnum):
    """Loop 结束原因全集 (loop 与 guard 共享): 自然完成 / 软限制 / 截断放弃.

    FINISHED / TRUNCATION_LIMIT 由 AgentLoop 判定; MAX_TURNS / TOKEN_BUDGET /
    TIME_LIMIT 由 LoopGuard.check_after_turn 判定.
    """

    FINISHED = "finished"  # 模型给出最终答案 (finish_reason=stop), 正常结束
    MAX_TURNS = "max_turns"  # 达到 max_turns 仍要继续调工具 → 强制停止
    TOKEN_BUDGET = "token_budget"  # 累计 token 达到 max_total_tokens 仍要继续
    TIME_LIMIT = "time_limit"  # 运行时长达到 max_duration_seconds 仍要继续
    TRUNCATION_LIMIT = "truncation_limit"  # 连续 length 截断超过重试上限


class TruncationStrategy(StrEnum):
    """length 截断的两种处理路径 (difficulties #10: 截断时结果不完整,
    要么续写、要么提示模型精简, 不能当正常答案返回)."""

    CONTINUE = "continue"  # 保留截断前缀, 提示模型接着中断处续写
    CONDENSE = "condense"  # 丢弃截断内容, 提示模型精简重答


@dataclass(slots=True)
class TurnRecord:
    """一次 Turn 的记录: 每轮结束时消息历史的完整快照 (供 checkpoint 落盘).

    response 保留本轮模型响应全文 —— 包括不入历史的 reasoning (#11) 与被
    CONDENSE 策略丢弃的截断内容; messages 是 wire 视角的浅拷贝快照
    (消息 dict 追加后不再变更, 浅拷贝即安全).
    """

    turn: int  # 轮次, 从 1 起
    response: ModelResponse
    messages: list[ModelMessage]
    tokens: int  # 本轮 usage 增量
    elapsed_ms: float  # 本轮结束时距 run 开始的累计耗时


@dataclass(slots=True)
class LoopResult:
    """AgentLoop.run 的结果: 完整历史 + 结束原因 + 每轮快照.

    attributes:
        messages: run 结束时完整消息历史 (wire), 供 checkpoint 落盘或
            继续下一轮对话 (调用方可直接作为下次 run 的输入).
        content: 最终答复正文; 无正文时为 None (纯工具调用收尾 / guard 触发 /
            截断放弃等). 截断续写 (CONTINUE) 场景最终答案跨多条 assistant
            消息 (截断前缀 + 续写段), 此处已由 AgentLoop 拼合为完整文本 ——
            分段原文仍可在 messages 与 turns 中查到 (保真不丢).
        finish_reason: 最后一次模型响应的终止原因 (tool_calls/length 等).
        outcome: 结束原因, 见 LoopOutcome.
        turns: 每轮快照 (TurnRecord), 顺序为执行序.
        turn_count: 模型决策次数 (恒等于 len(turns), 便捷字段免去取长度).
        truncation_count: 发生的 length 截断处理次数.
        total_tokens: 全 run 累计 usage (无 usage 的调用计 0).
        elapsed_ms: 墙钟总耗时.
    """

    messages: list[ModelMessage]
    content: str | None
    finish_reason: FinishReason | None
    outcome: LoopOutcome
    turns: list[TurnRecord]
    turn_count: int  # == len(turns), 便捷字段
    truncation_count: int = 0
    total_tokens: int = 0
    elapsed_ms: float = 0.0
