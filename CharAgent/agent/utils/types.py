"""agent 包共享数据结构与枚举: AgentLoop 的输入输出类型.

对齐 model/utils/types.py 的组织惯例 —— 行为 (AgentLoop.run 的循环语义)
在 agent/loop.py, 类型定义集中于此供 loop / guard / 门面共享引用.

- LoopOutcome: Loop 结束原因全集 (guard 触发点 + loop 分支放弃点)
- TruncationStrategy: length 截断的两种处理路径 (#10)
- TurnRecord: 每轮结束时的消息历史完整快照 (供 checkpoint 落盘)
- LoopState: AgentLoop 一次 run 的内存工作数据 (run 与其分支方法之间传递,
  内部零件; 注意与 RunState = 运行状态机不同, 见类 docstring)
- LoopResult: run 的完整结果 (历史 + 结束原因 + 每轮快照)

上下文压缩 (#7) 的两个字段 (summary / summary_covers) 也挂在 LoopState 上:
它们描述的是「账本被压到哪一步」, 与 turn_count 一样属于跨轮累积的进度, 要跟着
快照一起落盘 (于是续跑不必从头再压一遍). 视图本身 (CompiledView) 不在这里 ——
它每次模型调用前现算, 不落地, 见 agent/compaction.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
)


class LoopOutcome(StrEnum):
    """Loop 结束原因全集 (loop 与 guard 共享): 自然完成 / 软限制 / 截断放弃 / 上游中断.

    FINISHED / TRUNCATION_LIMIT / SERVER_INTERRUPTED 由 AgentLoop 判定;
    MAX_TURNS / TOKEN_BUDGET / TIME_LIMIT 由 LoopGuard.check_after_turn 判定.
    """

    FINISHED = "finished"  # 模型给出最终答案 (finish_reason=stop), 正常结束
    MAX_TURNS = "max_turns"  # 达到 max_turns 仍要继续调工具 → 强制停止
    TOKEN_BUDGET = "token_budget"  # 累计 token 达到 max_total_tokens 仍要继续
    TIME_LIMIT = "time_limit"  # 运行时长达到 max_duration_seconds 仍要继续
    TRUNCATION_LIMIT = "truncation_limit"  # 连续 length 截断超过重试上限
    # 服务端中断 (insufficient_system_resource / aborted): 生成被打断, 内容
    # 可能只是半截, 故不作最终答复返回. 资源不足属瞬态, 重放决策留给调用方
    # (框架重试归重试层)
    SERVER_INTERRUPTED = "server_interrupted"


# 服务端中断的 finish_reason (非模型自然结束): 生成被打断, content 可能只是
# 半截, 不能当最终答复返回 (官方语义见 model/utils/types.FinishReason);
# AgentLoop 据此判 LoopOutcome.SERVER_INTERRUPTED
SERVER_INTERRUPTED: frozenset[FinishReason] = frozenset(
    {
        FinishReason.INSUFFICIENT_SYSTEM_RESOURCE,
        FinishReason.ABORTED,
    }
)


class TruncationStrategy(StrEnum):
    """length 截断的两种处理路径 (difficulties #10: 截断时结果不完整,
    要么续写、要么提示模型精简, 不能当正常答案返回)."""

    CONTINUE = "continue"  # 保留截断前缀, 提示模型接着中断处续写
    CONDENSE = "condense"  # 丢弃截断内容, 提示模型精简重答


@dataclass(slots=True)
class LoopState:
    """AgentLoop 一次 run 的内存工作数据 (内部零件, 不经门面导出).

    名字为什么不叫 RunState: 这个名字已被「运行状态机」
    (created / running / waiting_tool / ... / cancelled, 持久化在 run 记录里,
    有合法迁移规则) 占用. 两者完全不同 —— 本类只是**内存里的
    可变数据袋** (没有任何迁移规则), run 结束时随 TurnRecord 快照被捕获;
    RunStatus 那种「状态 + 合法迁移 + 非法迁移拒绝」才是真正的状态机.

    为什么有这么一个类型: run 的 while 循环按功能拆成若干方法后 (可读性重构),
    这些方法都要读写同一批数据 (历史 / 轮次 / 累计用量 / 结束原因 ...). 逐个
    当参数传来传去会变成一长串 in/out 且容易漏改; 打包成一个对象后, 每个分支
    方法只收 (state, response), 读改了哪些字段在方法体里一眼可见.

    attributes:
        history: 完整 wire 消息历史 (逐轮 append; run 结束时即
            LoopResult.messages, 可直接续接下一轮对话).
        turns: 每轮结束时的历史快照 (TurnRecord), 顺序为执行序.
        content_parts: CONTINUE 截断续写已输出的正文前缀 (跨轮累积, 终止时
            与尾段拼合为最终答复).
        content: 最终答复正文; None 表示无正文 (纯工具收尾 / 刹车 / 截断放弃 /
            上游中断等).
        outcome: 结束原因; 初值 FINISHED, guard 触发点与各分支放弃点改写它.
        finish_reason: 最后一次模型响应的终止原因 (供 LoopResult 上报).
        turn_count: 已完成的模型决策次数 (全新 run 时 = len(turns); 断点续跑时
            含续跑前已完成的轮数, 于是轮数从 2、3 接着数, 不会从 1 重来).
        truncation_count: 已发生的 length 截断处理次数.
        total_tokens: 全 run 累计 usage (无 usage 的调用计 0; 续跑时接着累计).
        done: 循环是否结束 (分支方法置 True 表示这次 run 到此为止).
        run_id: 本次运行的编号 (配了 checkpoint saver 才有; 续跑时默认沿用快照里
            的 run_id —— 表示「还是同一次运行接着跑」).
        last_checkpoint_id: 最近落盘那一帧快照的编号. 下一帧的 parent_id 指向它,
            于是同一会话的快照串成一条链; 从老快照恢复时链就从那里岔出去, 形成
            新分支 (#5 time-travel).
        summary: 当前生效的上下文摘要 (#7); None 表示还没压过摘要. 它**不是**
            history 的一部分 —— 账本 (history) 永远是全量原文, 摘要只是「送给
            模型的视图」里那一段的替身; 跟着快照一起存, 于是续跑不必从头再压.
        summary_covers: 摘要覆盖到 history 的第几条 (前 summary_covers 条已被
            摘要取代). 第 0 条是 system, 永不裁也不进摘要. 压缩时靠它算出「哪些
            是这次新裁掉的」(滚动摘要要把上一条摘要连新段一起重压).
    """

    history: list[ModelMessage]
    turns: list[TurnRecord] = field(default_factory=list)
    content_parts: list[str] = field(default_factory=list)
    content: str | None = None
    outcome: LoopOutcome = LoopOutcome.FINISHED
    finish_reason: FinishReason | None = None
    turn_count: int = 0
    truncation_count: int = 0
    total_tokens: int = 0
    done: bool = False
    run_id: str | None = None
    last_checkpoint_id: str | None = None
    summary: str | None = None
    summary_covers: int = 0


@dataclass(slots=True)
class TurnRecord:
    """一次 Turn 的记录: 每轮结束时消息历史的完整快照 (供 checkpoint 落盘).

    response 保留本轮模型响应全文 —— 包括 reasoning (#11) 与被 CONDENSE
    策略丢弃的截断内容; messages 是 wire 视角的浅拷贝快照 (消息 dict
    追加后不再变更, 浅拷贝即安全).
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
            截断放弃 / 上游中断等). 截断续写 (CONTINUE) 场景最终答案跨多条 assistant
            消息 (截断前缀 + 续写段), 此处已由 AgentLoop 拼合为完整文本 ——
            分段原文仍可在 messages 与 turns 中查到 (保真不丢).
        finish_reason: 最后一次模型响应的终止原因 (tool_calls/length 等).
        outcome: 结束原因, 见 LoopOutcome.
        turns: **本次 run** 的逐轮快照 (TurnRecord), 顺序为执行序. 断点续跑时
            不含上一段 run 的轮次 —— 那些轮的明细在上一段的返回值与快照里.
        turn_count: 模型决策次数. 全新 run 恒等于 len(turns); 续跑时是**累计值**
            (含续跑前已完成的轮数), 那时它 >= len(turns) —— 预算判定要的正是这个
            累计口径 (轮数上限不因断点重启).
        truncation_count: 发生的 length 截断处理次数.
        total_tokens: 全 run 累计 usage (无 usage 的调用计 0; 摘要那几次调用也
            计在内 —— 它确实花了钱, 只是不占轮数).
        elapsed_ms: 墙钟总耗时.
        summary / summary_covers: 这一段 run 结束时生效的压缩进度 (#7). 要接着
            聊下一段, 就把它们连同 messages 一起递给 AgentLoop.run —— 于是滚动
            摘要跨 run 成立 (不然每段 run 都会把同一段旧历史重压一遍).
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
    summary: str | None = None
    summary_covers: int = 0
