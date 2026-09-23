"""上下文压缩 (difficulties #7): 账本 / 视图分离 —— 摘要 + 截断, 但不拆散 tool_calls.

一句话理解: 一个会话聊得越久, 每一轮都要把**全量历史**重发一遍 (会话 ID 在
前端标签页里是固定的, 历史只增不减), 成本与首字延迟随轮数线性上涨, 直到撞上
模型窗口. 本模块把「记下来的」与「送出去的」分开:

    账本 (LoopState.history)   append-only, 一字不改 —— 断点续跑与回溯靠它
       ↓ 投影 (每次模型调用前算一次, 不落地)
    视图 (送给模型的)          system + 摘要 + 摘要之后的一切; 账本再超阈值时
                               切一刀 (裁到最近 N 个提问 + 截短老工具结果)

**为什么不动账本**: ① 落盘的仍是全量历史 (只多了「压到哪一步」那两个字段) ——
于是断点续跑、time-travel 回溯、快照表格视图看到的都还是完整过程, 压缩掉的只是
这一次请求的输入; ② 「恢复不重跑」的前提是「做过的事都在历史里」, 原地删历史
就等于把那个前提拆了; ③ 投影是**纯函数**, 同样的账本 + 同样的参数 → 同样的视图,
好测也好解释.

三件套 (默认实现 TrimAndSummarize), 从省得多到省得细:

1. **窗口裁剪** —— 保 system (第 0 条) 与最近 N 个**提问**, 单位就是提问 (不是
   「一次模型决策」那个 turn). 刀口只落在提问的位置, 于是被裁掉的那段总是完整
   对话 (提问 + 为它做的全部决策), 保留的那段也总是完整对话.
2. **工具结果截断** —— 留着但已经很旧的工具正文截到阈值 (正在回答的那个提问
   之后的内容不截). 工具返回往往是大头, 截它比再丢一个提问划算 —— 骨架 (调过
   什么、结论是什么) 都还在.
3. **滚动摘要** —— 被裁掉的那段压成一段摘要, 下次压缩时**连上一条摘要一起重压**
   (不是只压新掉的那段: 那样更早的信息会被逐次稀释到消失).

四条硬不变量 (上游对消息配对的规矩是硬的, 破了直接 400 —— 见 #10):

- 绝不拆散 `assistant(tool_calls)` 与它的 `tool` 消息 (切点只落在提问处)
- 第 0 条 (system) 永不裁
- 至少保留最近 1 个提问完整 (裁到只剩 system 等于把这段对话删了)
- 裁完的序列仍是合法的 wire 序列

触发与水位线: 估算 ≥ `threshold_tokens` 才压; 压完要落到阈值乘水位线比例以下才
停手 (到不了就再丢一个提问, 一路丢到只剩最近 1 个). 水位线是**滞回** —— 只按
下限压的话, 下一次调用立刻又超线, 于是每次调用都压一次, 摘要调用也就每次都
花一次钱.

**投影与切刀是两件事** (2026-09-23 修, ticket 23): 投影**每一轮都做** (摘要替换掉
它覆盖过的那一段), 阈值只管「要不要再切一刀」(扩大覆盖 + 重新压摘要). 两者曾经被
合成一句 `if before < threshold: return unchanged` —— 而 `unchanged` 是**全量账本**,
于是压过之后每隔一轮视图就失效一次 (摘要根本不带上), 账本随即把估算顶回大值,
再下一轮又切一刀: 八轮模拟里 2 压 3 跳、4 压 5 跳, 单请求 token 一半的轮次跳回全量,
摘要调用也白烧一半. 分开之后同一份模拟是「每轮都带摘要, 请求随账本缓涨到阈值再切」.

与 LoopGuard 的分工 (两个数各自算, 都要留): guard 是**运行级刹车** (这次运行总共
别烧太多 → 直接停), 本模块是**单请求治理** (别让这一次请求变大 → 压完继续).

压掉的信息没有丢: 旧帧还在快照里 (`list_history` 可回溯). 真正的「长期记忆 +
按需检索」是另一个组件 (难点 #4 的情景记忆), 本模块不做 —— 边界写在这里, 免得
把「上下文里看不见」误当成「已经不存在了」.

为什么不挂在 BEFORE_TURN 钩子上 (它拿到的 messages 就是活引用, 原地改真的能改):
① 那是 fire 类点, 契约原话是「忽略, 没人看」—— 把「改变下一步输入」的逻辑塞进
观察点, 等于让 hook 契约变成「某些点其实可以改载荷」; ② 压缩需要**框架保证的
不变量** (配对 / system 不裁 / 至少留 1 个提问), 这是通用知识, 不该每个业务各写一遍;
③ 摘要是一次真实模型调用, 它的 usage 要计入运行预算, 钩子点拿不到那个回写口.

本模块只放行为 (估算器 / 策略 / 投影产物); 字符启发式与摘要文案在
utils/messages.py, 账本字段 (summary / summary_covers) 在 LoopState 与
CheckpointState 上 (v4 起随快照一起存).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from CharAgent.agent.utils.errors import CompactionConfigError
from CharAgent.agent.utils.messages import (
    estimate_tokens,
    summary_request,
    summary_view_message,
    truncate_text,
)
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import ModelMessage, Usage

logger = logging.getLogger("charagent.agent")

# 默认值 (业务按自己的窗口与账单调, 见 CharApp 的 CHARAPP_CONTEXT_*):
# 单请求 2.4 万 token 上下开始压 —— 对 6 万 token 的运行预算来说留了足够余量,
# 又远在 12.8 万窗口之下; 老工具结果截到 800 字够留住结论, 又不至于把长表格
# 整段背进下一次请求.
DEFAULT_THRESHOLD_TOKENS = 24_000
DEFAULT_KEEP_RECENT_QUESTIONS = 2
DEFAULT_WATERMARK_RATIO = 0.6
DEFAULT_TOOL_RESULT_LIMIT = 800
# 摘要那一次调用的预算 (2026-09-23 从 512 调到 1024, 与「摘要固定关思考」配套):
# 摘要是**滚动**的 (上一条摘要连新裁掉的段一起重压), 它的长度会随对话缓慢增长,
# 所以这里既是「给足一次压缩的空间」也是那道上限. 关掉思考之后 1024 装得下一段
# 带事实与结论的中文摘要, 而它**不进**运行预算的大头 —— 一次 1024 的输出远小于
# 它压掉的那些历史.
DEFAULT_SUMMARY_MAX_TOKENS = 1_024


@dataclass(frozen=True, slots=True)
class CompiledView:
    """一次投影的产物: 送给模型的消息 + 新摘要 + 「这次做了什么」.

    后一组字段是给事件与调试看的 (loop 拿它发 context_compacted, 也是「压缩到底
    省没省下来」的唯一数据源). 它们都是**估算**: 权威值仍然只有上游给的 usage.

    attributes:
        messages: 送给模型的这份消息列表 (账本的副本; 没压时内容与账本一致).
        summary: 新的摘要正文; None 表示还没有摘要 (没压过 / 摘要没生成出来).
        summary_covers: 摘要覆盖到账本的第几条 (前 summary_covers 条的信息已压进
            摘要). 第 0 条是 system, 永远不进摘要也不被裁.
        dropped: 这次裁掉几条消息.
        truncated: 这次截短几条工具结果.
        estimated_tokens: 压后估算 (这份视图作为一次请求大概多大).
        saved_tokens: 省下的估算量 (同一把尺子量的账本与视图之差; 压后反而更大
            时为负 —— 如实报, 不夹到 0).
        summarized: 这次走没走摘要 (False = 只做了裁剪).
        summarizer_usage: 摘要那一次调用的 usage (要计入本次运行预算, 但不占
            max_turns 的额度 —— 它不是一次模型决策).
        warning: 降级原因 (没有摘要模型 / 摘要失败); None 表示这次没出岔子.
    """

    messages: list[ModelMessage]
    summary: str | None = None
    summary_covers: int = 0
    dropped: int = 0
    truncated: int = 0
    estimated_tokens: int = 0
    saved_tokens: int = 0
    summarized: bool = False
    summarizer_usage: Usage | None = None
    warning: str | None = None

    @property
    def compacted(self) -> bool:
        """这次真的压了吗 —— 决定发不发 context_compacted 事件.

        判据是「有没有东西变了」而不是「试没试过」: 没超阈值、或者压不动
        (只有一个提问), 都不该在前端留一条「压缩过」的痕迹.
        """
        return self.dropped > 0 or self.truncated > 0


def compiled_counts(compiled: CompiledView) -> dict[str, Any]:
    """这次压缩「做了什么」的六个计数 (**唯一一处定义**).

    两个下游共用它, 各自再拼上自己多出来的那个键:

    | 谁 | 多出来的键 | 给谁看 |
    |---|---|---|
    | `utils/events.context_compacted_data` | `turn` | 前端的进度提示 (这一轮压了) |
    | `view_payload` | `messages` | 帧里的审计材料 (当时发出去的那份) |

    口径只写一遍的理由很直白: 加一个计数时漏掉一边, 表现是「事件里说了、帧里没
    说」(或反过来) —— 而那两边本来就是同一件事的两种说法.
    """
    return {
        "dropped": compiled.dropped,
        "truncated": compiled.truncated,
        "estimated_tokens": compiled.estimated_tokens,
        "saved_tokens": compiled.saved_tokens,
        "summarized": compiled.summarized,
        "warning": compiled.warning,
    }


def view_payload(
    compiled: CompiledView, ledger: Sequence[ModelMessage]
) -> dict[str, Any]:
    """这一轮**真的发出去**的东西 → 落进帧里的观察值 (ticket 22 第 4 件).

    为什么记它: 压缩让「账本」与「送给模型的」分成两份, 而库里原本只有账本 ——
    「当时它看到了什么」(L3 要回答的那句) 因此答不上来. 这一份补上.

    两条取值规则:
    - **`messages` 只在视图与账本不同时才带**. 相同时给 None, 语义是「**视图就是
      账本本身**」: 帧里已经有全量账本 (`state.messages`), 再抄一份会让每帧体积
      翻倍; 而审计价值全在「视图真的不一样的那些轮」.
    - 判据是**直接比**, 不是看 `compiled.compacted`: 两者今天恰好等价 (不切就不
      投影), 而那是 view-oscillation 那条缺陷 (2026-09-23 修) 的副产品 —— 修好
      之后「没切刀但视图仍带摘要」的轮会变成常态, 用 `compacted` 判会全漏掉.

    Args:
        compiled: 这一轮投影的产物.
        ledger: 同一时刻的账本 (用来判「视图与账本是否相同」).

    Returns:
        dict: 计数 (与 `context_compacted_data` 同一套口径) + `messages`.
    """
    same = compiled.messages == list(ledger)
    return {
        **compiled_counts(compiled),
        "messages": None if same else compiled.messages,
    }


class TokenCounter(Protocol):
    """SPI: 估算「这份消息列表作为一次请求, 上游会算多少输入 token」.

    两个方法都属于契约 (不是可选增强):

    - `count` 是纯查询, 策略拿它判阈值与水位线;
    - `note_usage` 是**权威值回灌** —— 上游的 usage 到了, 请更新你的锚. 真正
      权威的输入 token 数只有 API 给的 usage, 估算只用来判阈值; 真分词器
      (tiktoken / 官方 tokenizer) 不需要锚, 把这一条写成空实现即可. 框架刻意
      不做 isinstance 嗅探 (既有协议约定是结构性协议, 不用 runtime_checkable,
      见 agent/provider.py), 于是这条回灌口明写在协议上.
    """

    def count(self, messages: Sequence[ModelMessage]) -> int:
        """这份消息列表有多大 (估算).

        Args:
            messages: 待估算的消息 (账本或视图, 甚至是别的列表).

        Returns:
            int: 估算的输入 token 数 (含上游固定开销; 认不出来就给字符启发式).
        """
        ...

    def note_usage(self, usage: Usage | None, *, message_count: int) -> None:
        """上游给了这一次调用的真实用量, 更新估算的锚.

        Args:
            usage: 这一次响应的 usage; None 或没有 input_tokens 时锚不动
                (没有权威值就继续用上一个锚, 或退回启发式).
            message_count: 这一次请求发出**当时账本有几条** (账本是 append-only
                的, 于是「锚 + 之后新增那几条」永远算得回来).
        """
        ...


@dataclass(slots=True)
class AnchorTokenCounter:
    """默认估算器: 锚式估算 —— 锚 = 最近一次请求的真实 input_tokens.

    为什么拿上次的大小当基准, 而不是每次重新猜一遍整份历史: 上游给的 input_tokens
    里含**启发式看不见的固定开销** (工具 schema / 系统提示的模板部分), 那部分
    每次请求都差不多; 真正在变的只有新追加的几条消息. 于是估算 = 锚 + 增量, 增量
    才用字符启发式.

    锚记的是「当时**账本**有几条」(不是送出去视图有几条): 账本 append-only, 两次
    请求之间多出来的正好是账本尾部新增的那些, 一句话就能对上. 压缩过也算得回来 ——
    那时锚本身就是压完之后的值, 于是刚压完不会立刻再压一次 (滞回的另一半).
    """

    _anchor_tokens: int | None = field(default=None, init=False, repr=False)
    _anchor_count: int = field(default=0, init=False, repr=False)

    def note_usage(self, usage: Usage | None, *, message_count: int) -> None:
        """记下权威锚 (没有 input_tokens 就什么都不做, 见 TokenCounter)."""
        if usage is None or usage.input_tokens is None:
            return
        self._anchor_tokens = usage.input_tokens
        self._anchor_count = message_count

    def count(self, messages: Sequence[ModelMessage]) -> int:
        """锚 + 新增部分的启发式; 没锚 (或量的是更短的列表) 时整份走启发式."""
        if self._anchor_tokens is None or len(messages) < self._anchor_count:
            return estimate_tokens(messages)
        return self._anchor_tokens + estimate_tokens(messages[self._anchor_count :])


class CompactionPolicy(Protocol):
    """SPI: 把账本投影成这一次要发出去的视图 (业务可换自己的策略).

    默认实现见 TrimAndSummarize. `apply` 是**异步**的 —— 默认策略里有一次真实的
    模型调用 (摘要), 所以这条接缝只能是 async; 不调模型的策略照写 async 返回即可.

    两处纪律: ① **不得改动传进来的 history** (它是账本, 只读; 要改就返回副本);
    ② 实现自己抛的异常**会中断这次 run** (策略是框架能力的一部分, 不是旁挂插件;
    默认策略里唯一被容忍的失败是「摘要那一次模型调用」, 它降级不抛).
    """

    async def apply(
        self,
        history: Sequence[ModelMessage],
        *,
        summary: str | None,
        summary_covers: int,
        counter: TokenCounter,
        summarizer: ChatModel | None = None,
    ) -> CompiledView:
        """算这一次的视图 (纯投影: 不改 history, 不落盘).

        Args:
            history: 账本 (append-only 的消息历史); 实现不得改动它.
            summary: 上一条摘要 (滚动摘要要连它一起重压); None 表示还没摘要.
            summary_covers: 上一条摘要覆盖到账本的第几条.
            counter: 估算器 (判阈值与水位线都用它).
            summarizer: 摘要用哪个模型 (默认策略优先用自己注入的那个,
                None 时用这个 —— 主模型). None 表示没有可用的摘要模型.

        Returns:
            CompiledView: 视图 + 新摘要 + 「这次做了什么」.
        """
        ...


@dataclass(slots=True)
class TrimAndSummarize:
    """默认策略: 窗口裁剪 + 工具结果截断 + 滚动摘要 (difficulties #7).

    attributes:
        threshold_tokens: 估算达到它就压 (压完要落到水位线以下).
        keep_recent_questions: 至少留几个完整的提问 (一个提问连同为它做的全部
            决策; 不是「一次模型决策」那个 turn). 到不了水位线时从它开始一个一个
            往下丢, 但不会低于 1 个.
        watermark_ratio: 水位线比例 —— 压到阈值的这个倍数以下才停手.
        tool_result_limit: 老工具结果的正文截到多少字符 (正在回答的那个提问的
            内容不截).
        summary_max_tokens: 摘要那次调用的 max_tokens (只影响摘要本身). 它是**滚动
            摘要的长度上限**, 也是「关掉思考之后正文还装得下」的那点余量 (见
            `_summarize`: 那一次调用固定 `thinking=False`).
        summarizer: 摘要模型; None 表示用 apply 传进来的那个 (通常是主模型).
        summarize: 要不要走滚动摘要. False = 只做窗口裁剪 + 工具结果截断 (三件套
            的第三件关掉), 于是压缩这一步**一次模型调用都不发**. 这不是降级: 摘要
            只为「压得更狠」而存在, 它每压一次就要花一次钱 —— 花不花由业务按自己
            的账单决定 (CharApp 的 `CHARAPP_CONTEXT_SUMMARY` 就是它).
    """

    threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS
    keep_recent_questions: int = DEFAULT_KEEP_RECENT_QUESTIONS
    watermark_ratio: float = DEFAULT_WATERMARK_RATIO
    tool_result_limit: int = DEFAULT_TOOL_RESULT_LIMIT
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS
    summarizer: ChatModel | None = None
    summarize: bool = True

    def __post_init__(self) -> None:
        """配置校验 (与 LoopGuard / AgentLoop 同一条纪律: 配置错在装配时报)."""
        if self.threshold_tokens < 1:
            raise CompactionConfigError(
                f"threshold_tokens 必须 >= 1, 实际: {self.threshold_tokens}"
            )
        if self.keep_recent_questions < 1:
            raise CompactionConfigError(
                f"keep_recent_questions 必须 >= 1 (至少留 1 个提问), 实际: "
                f"{self.keep_recent_questions}"
            )
        if not 0 < self.watermark_ratio < 1:
            raise CompactionConfigError(
                f"watermark_ratio 必须在 0 与 1 之间 (它是阈值的比例), 实际: "
                f"{self.watermark_ratio}"
            )
        if self.tool_result_limit < 1:
            raise CompactionConfigError(
                f"tool_result_limit 必须 >= 1, 实际: {self.tool_result_limit}"
            )
        if self.summary_max_tokens < 1:
            raise CompactionConfigError(
                f"summary_max_tokens 必须 >= 1, 实际: {self.summary_max_tokens}"
            )

    async def apply(
        self,
        history: Sequence[ModelMessage],
        *,
        summary: str | None,
        summary_covers: int,
        counter: TokenCounter,
        summarizer: ChatModel | None = None,
    ) -> CompiledView:
        """算这一轮的视图: **投影每轮都做**, 阈值只管「要不要再切一刀」.

        两件事曾经被合成一件 (2026-09-23 修, ticket 23):

        | | 什么时候做 | 做什么 |
        |---|---|---|
        | **投影** | **每一轮** | 视图 = 第 0 条 + 摘要 + 摘要没覆盖到的一切 |
        | | | (没摘要时 = 账本本身) |
        | **再切一刀** | 账本估算到阈值时 | 扩大摘要覆盖 (重压摘要) |
        | | | + 裁到最近 N 个提问 + 截短老工具结果 |

        合并的代价是真机上踩出来的: 压完那一轮的锚落到「小视图」上 → 下一轮估算
        (小锚 + 增量) 低于阈值 → 判定「不用压」→ 而当时那句 `unchanged = list(history)`
        把**全量账本**原样发了出去, 摘要那条根本不带上. 于是视图每隔一轮失效一次,
        而账本随即把锚顶回大值 → 再下一轮又切一刀 (又烧一次摘要) —— 八轮模拟里
        2 压 3 跳、4 压 5 跳, 单请求 token 一半的轮次跳到全量.

        顺序 (修好之后): **先投影** (不花钱的那一半) → **再判要不要切** (锚值最可信
        的一步) → **最后才压摘要** (真裁掉了东西才有得压, 而且这一步要花钱). 摘要
        失败只降级、不抛 —— 那是一次「压得更好看」的调用, 不是这条链路成立的必需件.

        Note:
            投影只丢**摘要已经覆盖过的**那一段 (切点 `max(summary_covers, 1)`) ——
            没进过摘要的原文一条都不丢, 否则信息就凭空没了.
        """
        before = counter.count(history)
        # 投影 (每轮都做): 摘要替换掉它覆盖的那一段, 老工具结果**不截** (截断与裁剪
        # 同属「再切一刀」那一半, 见 _view 的 truncate_tools)
        projected, _ = self._view(
            history,
            cut=max(summary_covers, 1),
            summary=summary,
            latest_start=_latest_question_start(history),
            truncate_tools=False,
        )
        unchanged = CompiledView(
            messages=projected,
            summary=summary,
            summary_covers=summary_covers,
            # 没切刀: dropped / truncated / summarized 都保持「什么都没做」,
            # 于是不发 context_compacted 事件 (上一次压缩已经报过一次了);
            # 但它确实比账本小 —— saved_tokens 如实报
            estimated_tokens=counter.count(projected),
            saved_tokens=estimate_tokens(history) - estimate_tokens(projected),
        )
        if before < self.threshold_tokens:
            return unchanged

        cut = self._choose_cut(history, summary=summary, counter=counter)
        if not cut:
            # 压不动 (只有一个提问 / 没有可切的提问): 如实报告什么都没做
            return unchanged

        if self.summarize:
            new_summary, usage, warning = await self._summarize(
                history[max(summary_covers, 1) : cut],
                previous=summary,
                model=summarizer,
            )
        else:
            # 摘要整个关掉: 连一次调用都不发, 且**不留降级原因** —— `warning` 那
            # 个字段是留给「本来要摘要却没成」的, 拿它报一个配置选择会让每一次
            # 压缩看起来都出了岔子 (见 CompiledView.warning)
            new_summary, usage, warning = None, None, None
        summarized = new_summary is not None
        final_summary = new_summary if summarized else summary
        view, truncated = self._view(
            history,
            cut=cut,
            summary=final_summary,
            latest_start=_latest_question_start(history),
        )
        return CompiledView(
            messages=view,
            summary=final_summary,
            # 摘要没生成出来就不推进 covers: 那段还没进过摘要, 下次压还得带上它
            summary_covers=cut if summarized else summary_covers,
            dropped=cut - 1,
            truncated=truncated,
            estimated_tokens=counter.count(view),
            saved_tokens=estimate_tokens(history) - estimate_tokens(view),
            summarized=summarized,
            summarizer_usage=usage,
            warning=warning,
        )

    # ------------------------------------------------------------------
    # 切点与视图 (纯计算)
    # ------------------------------------------------------------------

    def _choose_cut(
        self,
        history: Sequence[ModelMessage],
        *,
        summary: str | None,
        counter: TokenCounter,
    ) -> int:
        """定切点: 从 keep_recent_questions 起一个提问一个提问往回收,
        直到估算落到水位线以下.

        全都不达标时取最后一刀 (keep = 1, 还留着最近那个提问) —— 压不到也得压,
        否则一份长期过大的账本会永远不动 (滞回是为了少压, 不是为了不压).

        Returns:
            int: 账本从第几条开始保留; 0 表示没得裁 (此时一个字都不动).
        """
        target = int(self.threshold_tokens * self.watermark_ratio)
        latest_start = _latest_question_start(history)
        chosen = 0
        for keep in range(self.keep_recent_questions, 0, -1):
            cut = _cut_index(history, keep)
            if not cut:
                continue
            trial, _ = self._view(
                history,
                cut=cut,
                summary=summary,
                latest_start=latest_start,
                summary_slot=True,
            )
            chosen = cut
            if counter.count(trial) <= target:
                break
        return chosen

    def _view(
        self,
        history: Sequence[ModelMessage],
        *,
        cut: int,
        summary: str | None,
        latest_start: int,
        summary_slot: bool = False,
        truncate_tools: bool = True,
    ) -> tuple[list[ModelMessage], int]:
        """切出视图: 第 0 条 + (摘要) + 第 cut 条起, 其中老工具正文截短.

        Args:
            summary: 放进视图的摘要正文.
            summary_slot: 没有摘要时也占一个空位 —— 给**试算**用. 压完摘要那条
                消息一定会在位 (它是「被裁掉那段」的唯一记录), 试算时漏掉它,
                水位线就会卡在边上 (压完刚好又超一点).
            truncate_tools: 要不要顺手截短老工具结果. **投影那一路给 False** ——
                截断是「再切一刀」那一半的手段 (与裁剪同进退), 而**投影每一轮都做**
                (见 apply): 不切刀的那一轮只把摘要放回去, 不该顺手改正文 (2026-09-23,
                ticket 23).

        Returns:
            tuple: (视图消息, 这次截短了几条工具结果).
        """
        view: list[ModelMessage] = [history[0]]
        if summary or summary_slot:
            view.append(summary_view_message(summary or ""))
        truncated = 0
        for index in range(cut, len(history)):
            message = history[index]
            content = message.get("content")
            if (
                truncate_tools
                and message.get("role") == "tool"
                and index < latest_start  # 正在回答的那段不截
                and isinstance(content, str)
                and len(content) > self.tool_result_limit
            ):
                message = {
                    **message,
                    "content": truncate_text(content, limit=self.tool_result_limit),
                }
                truncated += 1
            view.append(message)
        return view, truncated

    # ------------------------------------------------------------------
    # 摘要 (唯一会花钱的一步)
    # ------------------------------------------------------------------

    async def _summarize(
        self,
        dropped_messages: Sequence[ModelMessage],
        *,
        previous: str | None,
        model: ChatModel | None,
    ) -> tuple[str | None, Usage | None, str | None]:
        """把「上一条摘要 + 这次新裁掉的段」压成新摘要 (失败降级, 不抛).

        Args:
            dropped_messages: 这次新裁掉的那些消息 (要被压进摘要的材料).
            previous: 上一条摘要 (滚动摘要连它一起重压).
            model: 没注入 summarizer 时用哪个模型 (通常是主模型).

        Returns:
            tuple: (新摘要正文, 那一次调用的 usage, 降级原因). 正文为 None
            表示没生成出来, 此时原因非 None (拿不到正文就没摘要可更新, 二者
            是同一个事实, 不另设一个布尔).
        """
        if not dropped_messages:
            return None, None, None
        chosen = self.summarizer or model
        if chosen is None:
            logger.warning("没有可用的摘要模型, 本次压缩降级为纯裁剪")
            return None, None, "没有可用的摘要模型, 本次只做裁剪"
        request = summary_request(
            previous, dropped_messages, tool_limit=self.tool_result_limit
        )
        try:
            # thinking=False 是**钉死的常量, 不是参数** (2026-09-23 真机实测):
            # 不传它时这一次调用落回适配器的上游默认 (`thinking=None` = 开启), 而
            # 思考会先把 max_tokens 吃掉 —— 正文为空, 框架按「摘要失败」降级纯裁剪.
            # 真机表现是「摘要这一步在生产里从没生效过」(库里 9 帧只有 2 帧带摘要,
            # 且其中一帧还是临时关掉思考才跑出来的). 摘要是一次**机械压缩**, 思考
            # 在这件事上买不到什么, 所以不设开关 —— 要生效就得每一家都记得关,
            # 那正是它漏掉整片功能的原因.
            response = await chosen.generate(
                request,
                None,
                max_tokens=self.summary_max_tokens,
                thinking=False,
            )
        except Exception as exc:
            # 摘要只是「压得更好看」, 不是必需件: 它失败不该让用户这一句问不出来
            # (降级纯裁剪; 留痕两处: 日志 + 视图上的 warning). 刻意不吞
            # CancelledError —— 它继承 BaseException, kill switch 照样打得断.
            logger.warning("摘要生成失败, 本次压缩降级为纯裁剪: %r", exc)
            reason = f"摘要没能生成 ({type(exc).__name__}), 本次只做裁剪"
            return None, None, reason
        text = (response.content or "").strip()
        if not text:
            logger.warning("摘要模型没给出正文, 本次压缩降级为纯裁剪")
            return None, response.usage, "摘要模型没有给出正文, 本次只做裁剪"
        return text, response.usage, None


def _cut_index(history: Sequence[ModelMessage], keep: int) -> int:
    """保留最近 keep 个提问时, 账本从第几条开始保留 (也是摘要覆盖的前缀长度).

    切点只落在**提问**处: 于是保留段与被裁掉的段各自都是完整对话 —— assistant 与
    它的 tool 结果永远同进退 (#10 的配对规矩, 破了上游直接 400).

    Returns:
        int: 切点下标; 0 表示没得裁 —— 不够 keep 个提问, 或者切完只剩 system 一条,
        或者被裁掉的那段里根本没有一次完整的模型决策 (光秃秃的提问不算) .
    """
    starts = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(starts) < keep:
        return 0
    cut = starts[-keep]
    if cut <= 1:
        return 0
    if not any(m.get("role") == "assistant" for m in history[1:cut]):
        return 0
    return cut


def _latest_question_start(history: Sequence[ModelMessage]) -> int:
    """正在回答的那个提问的下标 (最后一个 user 消息的位置); 没有提问时返回 len(history).

    用处只有一个: 「正在回答的那段不截」—— 模型正拿着这份工具结果答这一句,
    截了就是答非所问. 返回 len(history) 表示没有受保护的那段 (全都可以截).
    """
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            return index
    return len(history)
