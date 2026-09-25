"""agent 包共享数据结构与枚举: AgentLoop 的输入输出类型.

对齐 model/utils/types.py 的组织惯例 —— 行为 (AgentLoop.run 的循环语义)
在 agent/loop.py, 类型定义集中于此供 loop / guard / 门面共享引用.

- LoopOutcome: Loop 结束原因全集 (guard 触发点 + loop 分支放弃点 + 挂起等人)
- TruncationStrategy: length 截断的两种处理路径 (#10)
- ToolCallOutcome / ToolCallFact: 一次工具调用的事实 (落库协作者据此写
  `charagent_tool_calls`, DESIGN #41 的 tool_call 那一层)
- ApprovalRequest / Approval: 挂起与恢复的两头 —— 一条调用被裁决为「需人工确认」
  时停下的样子, 以及人给出的结论 (#25 HITL)
- TraceSink: 运行**进行中**把刚产生的东西交给记录层的出口 (可选零件, ticket 27)
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

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from CharAgent.agent.utils.errors import LoopConfigError
from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelToolCall,
)


class LoopOutcome(StrEnum):
    """Loop 结束原因全集 (循环与 guard 共享): 自然完成 / 软限制 / 截断放弃 /
    上游中断 / 挂起等人.

    FINISHED / TRUNCATION_LIMIT / SERVER_INTERRUPTED / SUSPENDED 由 AgentLoop
    判定; MAX_TURNS / TOKEN_BUDGET / TIME_LIMIT 由 LoopGuard.check_after_turn 判定.
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
    # 挂起等人给结论 (#25 HITL): 模型这一轮要调一个需要用户本人确认的工具, 于是
    # **不执行它**, 把进度连同那条欠着的调用一起存档等人. 它**不是**结束 —— 人给
    # 了结论就从存档点接着跑 (同一次运行的下一段), 所以下游要能把它与「这一轮答完
    # 了」分开: 前者不收尾、不写终态, 后者才收尾
    SUSPENDED = "suspended"


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


class ToolCallOutcome(StrEnum):
    """一次工具调用的状态 (agent 侧词汇, 记录层翻成 `db` 的状态值).

    四个取值对着 `db.entities.ToolCallStatus` 里框架自己会产生的那四个. 为什么不
    直接用那边的枚举: `agent/` 不 import `db/` (而 `db/state.py` 反向依赖本模块,
    双向会成环), 于是映射表放在 db 侧 (`db/state.py` 的 `TOOL_CALL_STATUS_FOR_OUTCOME`),
    与 `LoopOutcome → RunStatus` 同一套做法.
    """

    PENDING = "pending"  # 模型刚发起, 还没开始执行
    SUCCEEDED = "succeeded"  # 执行成功 (result 是回填文本)
    FAILED = "failed"  # 执行失败 / 被裁决拒绝 (result 是可操作原因)
    NEEDS_APPROVAL = "needs_approval"  # 挂起等人工批准 (#25); 产生挂起归 issue 34


@dataclass(slots=True, frozen=True)
class ToolCallFact:
    """一次工具调用留下的事实 (loop 记, 落库协作者据此写 `charagent_tool_calls`).

    为什么由 loop 交出来而不是让记录层自己去 wire 历史里挖: 工具名与参数在历史里
    找得到, 但**结果与耗时**只有执行处知道 (`tool/executor.py` 的 `ToolExecution`),
    而它随那一轮结束就没了. 于是 loop 每轮把这几样打成一条事实交出去 —— 运行中那
    两拍与收尾的「补齐」读的是同一批事实 (单一来源, 两个消费方).

    attributes:
        message_index: 发起它的那条 assistant 消息在**本次 run** wire 历史里的下标.
            记录层按它算 `message_id` (`db/repositories/messages.message_id_for`) ——
            同一次运行的第二段能直接寻址第一段那一行, 靠的就是这个下标.
        tool_call_id: 模型给的调用编号 (上游每轮从 `call_0` 重新编号, 所以真正唯一的
            身份是「哪条消息发起的这一次调用」).
        tool_name: 工具名.
        arguments: 模型填的**原样 JSON 字符串** (不预解析: 畸形 JSON 正是自纠错路径
            的信号).
        outcome: 这一条现在的状态 (执行前那一拍是 PENDING).
        result: 回填给模型的文本 (成功) 或可操作原因 (失败); 还没结论时为 None.
        duration_ms: 执行耗时 (毫秒); None = 还没跑到计时那一步 (与 0 毫秒是两回事).
        approval_prompt / approval_needs: 只有被裁决为**需人工确认**的那一条才带
            (挂起等人, #25): 给用户看的那句话术与还缺什么. 落库后刷新页面, 前端
            靠库里这两列加上 `tool_name` 就能重建那张确认卡 (issue 36). 其余情形
            是空串 / 空元组 —— 「不是挂起」与「挂起了但没什么话要说」在本框架里
            不会同时出现 (挂起必有话术, 见 Decision 的构造期校验).
    """

    message_index: int
    tool_call_id: str
    tool_name: str
    arguments: str
    outcome: ToolCallOutcome = ToolCallOutcome.PENDING
    result: str | None = None
    duration_ms: int | None = None
    # 挂起那一条才会有 (裁决为「需人工确认」时随事实一起交出去): 给用户看的那句
    # 话术与还缺什么. 记录层拿它们填 `charagent_tool_calls` 那两列 —— 于是刷新
    # 页面之后, 前端靠库里那一行就能**重建**那张确认卡 (issue 36 的刷新恢复)
    approval_prompt: str = ""
    approval_needs: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ApprovalRequest:
    """一条调用被裁决为「需人工确认」: 工具没执行, 整次运行就此停在半路等人.

    它是**裁决的结果**, 不是执行的结果 —— 所以与 `ToolExecution` 分开: 那一类的
    每一件都会变成一条回填给模型的工具消息 (连「结果未知」那种占位也算), 而这一
    件**什么都不回填**: 历史里那条 assistant 消息的这次调用就那么欠着, 直到有人
    给出结论. 「欠着一份结果」正是挂起的形状 (`checkpoint/utils/pending.py` 就是
    按它把欠着的调用找出来的), 恢复时补做的也是它.

    attributes:
        call: 那条要人批的调用 (原样, 恢复时按它补做).
        prompt: 给用户看的一句话 (来自 `Decision.requires_approval`, 框架只搬运).
        needs: 机器可读的缺失项 (同上); 空元组 = 纯是 / 否的确认.
        turn: 它发生在第几轮 (与 tool_call 事件同一个口径: 前端按它在会话流里
            原位插那张确认卡).
    """

    call: ModelToolCall
    prompt: str
    needs: tuple[str, ...] = ()
    turn: int = 0


# 人拒绝一次挂起时, 回填给模型的那句话 (缺省文案; 调用方可以给更贴上下文的一句).
# 与 hooks 的 INTERCEPT_FAILED_REASON 同一个位置: 都是「一条工具调用没执行」的
# 事实性说明 + 劝退重试, 由框架写 —— 面向**模型**, 不是给用户看的文案.
# 「不要重试」这句必须写: 模型收到一条失败结果的本能是换个参数再试, 而再试一次
# 就是再弹一张确认卡 (用户刚刚说不).
APPROVAL_REJECTED_TEXT = (
    "用户没有批准这次操作: 这一步没有执行, 也不要重试它 —— "
    "请如实告知用户, 并在需要时给出不依赖它的别的做法"
)


@dataclass(frozen=True, slots=True)
class Approval:
    """人对一次挂起的结论 (恢复那一头的入口参数): 批了, 还是拒了.

    与 `Decision` 的三态**不是一回事**, 别混: 那个是**插件在运行中**表的态 (要不
    要停下来问人), 这个是**人给出的结论** (问完了, 接着跑). 恢复必须带上它 ——
    挂起点欠着的调用可能就是付款, 而恢复的默认动作正是「把它补做完」, 没有结论
    就补做等于框架替人按了确认键 (DESIGN #25「绝不自动执行」).

    attributes:
        approved: True = 批准 (欠着的调用照常补做); False = 拒绝 (它们不执行, 原因
            当作工具结果回填, 模型据此继续答 —— 拒绝**也要恢复**, 这与「当场
            拒绝」是两条路).
        reason: 拒绝的原因 (面向模型的一句话); 批准时必须为 None.
    """

    approved: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        """构造期校验 (与 Decision 同一套取舍): 拒绝必须有原因, 批准不带原因."""
        if not self.approved and not (self.reason or "").strip():
            raise LoopConfigError(
                "拒绝一次挂起必须给出原因 (该原因会作为那条工具调用的结果回填给"
                "模型): 用 Approval.reject('为什么不行') 构造"
            )
        if self.approved and self.reason is not None:
            raise LoopConfigError(
                f"批准不该带原因 (没人会读它): reason={self.reason!r}, "
                "用 Approval.approve() 构造"
            )

    @classmethod
    def approve(cls) -> Approval:
        """批准 (欠着的调用照常补做)."""
        return cls(approved=True)

    @classmethod
    def reject(cls, reason: str | None = None) -> Approval:
        """拒绝 (不给原因就用框架那句缺省文案, 见 APPROVAL_REJECTED_TEXT)."""
        return cls(approved=False, reason=reason or APPROVAL_REJECTED_TEXT)


@runtime_checkable
class TraceSink(Protocol):
    """落库协作者: 运行**进行中**把刚产生的东西交出去 (可选零件, ticket 27).

    `runtime_checkable` 是**装配期要用**的: 会话拿到的记录员是业务给的任意对象
    (协议只按形状认), 于是装配时要问一句「它实现了这个可选能力没有」——
    `isinstance(recorder, TraceSink)` 只查方法在不在, 与结构匹配那套一致.

    与 `event_sink` (展示出口) 并列的第二个出口: 那个推给前端看, 这个落进记录表.
    为什么要它: 记录层从「收尾一次性写」改成「产生即落库」—— 提问那一行在提问时
    写, 每轮产生的消息与工具调用随轮写. 于是**工具调用行能在执行前落 pending、
    执行后回填结果**, 而挂起的那一行在挂起那一刻就在库里 (ADR-0014 要的那条判据).

    **它是可选的**: 没给、或业务自己的记录员没实现这个方法, 就退回「收尾一次性写」,
    行为与从前逐字一样. 与 `RunRecorder` (db 侧协议) 的关系: 同一个对象
    (`db/recorder.py` 的 `ConversationRecorder`) 两侧都实现, 而 loop 只认本协议
    (agent 不 import db).

    **实现方不该抛**: 记录写不进去从来不该让一次问答变成一次失败 (与
    `RunRecorder` 同一条规矩) —— 自己记日志并降级.
    """

    async def flush(
        self,
        *,
        thread_id: str,
        run_id: str,
        start: int,
        messages: Sequence[ModelMessage],
        calls: Sequence[ToolCallFact] = (),
    ) -> None:
        """把「刚从第 start 条起产生的这些消息」与「这几条调用的事实」落库.

        Args:
            thread_id: 哪段会话.
            run_id: 哪次运行 (会话在建行时定下, 一路传到 loop; 为 None 时 loop
                压根不调本方法 —— 没开账就没有可归属的行).
            start: `messages` 的第 0 条在本次 run wire 历史里的下标 (算编号用).
            messages: 这一次新增的 wire 消息 (提问 / assistant 隐藏行 / tool 回填行).
            calls: 与它们相关的调用事实 (执行前那一拍是 PENDING, 执行后是终态).
        """
        ...


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
        loop_id: 本次**循环执行**的编号 (配了 checkpoint saver 才有; 续跑时默认沿用
            快照里的 `loop_id` —— 表示「还是同一次执行接着跑」). 落进每一帧的
            `loop_id` 列, 一个 run 的几帧共享它.
        run_id: 本次运行在**记录层**是哪一行 (`charagent_runs.run_id`); 由会话层
            在建行时定下 (`RunRecorder.begin`), 框架只当一个不透明的值盖到每一帧上.
            None = 这一轮没记账 (没配记录层 / 建行没成).
        view: 这一轮**真的发出去**的那份 (装好的观察值, 见 `compaction.view_payload`);
            每轮覆盖, 落帧时进 `CheckpointMetadata.view` (ticket 22 第 4 件) ——
            「当时它发出去的是什么」由此可查. None = 这一轮还没投影过 (挂起补做那
            一轮没有模型调用) 或压根没配压缩策略.
        approval: 本次运行**停在谁那儿等人** (#25 HITL); None = 没挂起. 它一出
            现, 这次运行就到此为止 (`done` 同时置 True): 挂起帧要落盘 (进度 + 欠着
            的调用), 终局事件也换成「需人工确认」那一个. 落帧时由它推出
            `CheckpointState.suspension` (两份表示同源, 见 `_save_checkpoint`).
        last_checkpoint_id: 最近落盘那一帧快照的编号. 下一帧的 parent_id 指向它,
            于是同一会话的快照串成一条链; 从老快照恢复时链就从那里岔出去, 形成
            新分支 (#5 time-travel).
        summary: 当前生效的上下文摘要 (#7); None 表示还没压过摘要. 它**不是**
            history 的一部分 —— 账本 (history) 永远是全量原文, 摘要只是「送给
            模型的视图」里那一段的替身; 跟着快照一起存, 于是续跑不必从头再压.
        summary_covers: 摘要覆盖到 history 的第几条 (前 summary_covers 条已被
            摘要取代). 第 0 条是 system, 永不裁也不进摘要. 压缩时靠它算出「哪些
            是这次新裁掉的」(滚动摘要要把上一条摘要连新段一起重压).
        prompt_ref: 本 run 用的身份说明的**引用** (名字 + 渲染后正文的 sha256,
            见 prompt/ref.py). loop **不解释它**: 它只是被原样搬进快照的进度
            (落盘时按它把 messages[0] 的正文换成引用, 见 serialization.py).
            None 表示这一段的 history 第 0 条就是身份说明正文本身, 照原样存.
        input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
        cache_miss_tokens: 全 run 累计用量的五个**分量** (#34 成本归因), 与
            total_tokens 同口径 (含摘要那几次调用). **None = 上游一次都没上报过
            这个分量**, 与 0 (报过、值就是零) 是两回事 —— 累加规则见
            `messages.accumulate_usage`.
        flushed: 已经交给落库协作者 (`TraceSink`) 的消息条数 —— 历史前 flushed 条
            都不必再交 (ticket 27 的「产生即落库」按它切出每轮新增的那一段).
            初值是**本次 run 起点的历史长度**: 续跑时起点之前那些消息属于上一段
            (或上一次运行), 不该按本次 run 记一遍.
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
    loop_id: str | None = None
    run_id: str | None = None
    flushed: int = 0
    last_checkpoint_id: str | None = None
    view: dict[str, Any] | None = None
    approval: ApprovalRequest | None = None
    summary: str | None = None
    summary_covers: int = 0
    prompt_ref: dict[str, str] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None


@dataclass(slots=True)
class TurnRecord:
    """一次 Turn 的记录: 每轮结束时消息历史的完整快照 (供 checkpoint 落盘).

    response 保留本轮模型响应全文 —— 包括 reasoning (#11) 与被 CONDENSE
    策略丢弃的截断内容; messages 是 wire 视角的浅拷贝快照 (消息 dict
    追加后不再变更, 浅拷贝即安全).

    attributes:
        turn / response / messages / tokens / elapsed_ms: 见 `LoopResult.turns`.
        calls: 本轮调用的那几个工具留下的事实 (含结果与耗时, ticket 27). 空列表 =
            这一轮没调工具 (纯答复 / 截断 / 上游中断). 它是 `ToolCallFact` 的唯一
            来源: 运行中那两拍把它交给落库协作者, 收尾的「补齐」读的也是它.
    """

    turn: int  # 轮次, 从 1 起
    response: ModelResponse
    messages: list[ModelMessage]
    tokens: int  # 本轮 usage 增量
    elapsed_ms: float  # 本轮结束时距 run 开始的累计耗时
    calls: list[ToolCallFact] = field(default_factory=list)


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
        approval: 这一段**停在哪儿等人** (#25 HITL); None = 没挂起. 非空时
            `outcome` 必是 SUSPENDED, 且「终局事件」是 `approval_required` 而不是
            final (由 `emit_terminal` 从它派生) —— 前端据此渲染确认卡, 而调用方
            要恢复这次运行就得把人的结论递回来 (见 AgentLoop.resume 的 approval).
        last_checkpoint_id: 这一段 run 落的**最后一帧**快照编号; None 表示没配
            saver (或这一帧都没落成). 接着问下一句时把它当 `run(parent_id=...)`
            递回去, 同一段会话的快照就连成一条链而不是每次提问多一条新根.
        prompt_ref: 这一段 run 用的身份说明的引用 (名字 + 正文 sha256); None 表示
            装配时没给 (那这一段就没有可以据以还原的身份说明). 记录层拿它填
            `runs.prompt_version` —— 「这一轮是哪一版提示词答的」, 复盘与 A/B 都
            靠它 (#40), 而帧里那五个归因分量说不清这件事.
        input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
        cache_miss_tokens: 全 run 累计用量的五个**分量** (#34 成本归因); 与
            total_tokens 同口径、同一次累加里算出来的. **None = 上游一次都没
            上报过这个分量** (与 0 是两回事, 见 messages.accumulate_usage).
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
    last_checkpoint_id: str | None = None
    approval: ApprovalRequest | None = None
    prompt_ref: dict[str, str] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
