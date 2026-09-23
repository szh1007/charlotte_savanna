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

**判据量的是投影, 不是账本** (2026-09-23 改, ticket 26): 账本 append-only 只增不
减, 拿它判的话压完一次就永远超线 —— 每轮都切一刀、每轮都烧一次摘要. 投影才是
真正要发出去的东西, 它因为上一刀已经推过切点而变小, **滞回**就是这么来的.

三道闸决定「这一次到底压不压」: ① 投影得超阈值; ② 账本减试算视图得**是正的**
(压完反而更大就别压 —— 摘要那条自身也占地方); ③ 还得**够本**
(`clear_at_least_ratio`, 花一次摘要调用只省几百 token 不划算). 被 ③ 挡下的那一次
会在视图载荷里留 `skipped` 说明原因.

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
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from CharAgent.agent.utils.errors import CompactionConfigError
from CharAgent.agent.utils.messages import (
    REASONING_CLEARED_TEXT,
    cap_summary_material,
    estimate_tokens,
    summary_request,
    summary_view_message,
    truncate_text,
)
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.config import THINKING_OFF_EFFORT
from CharAgent.model.utils.types import FinishReason, ModelMessage, Usage

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
# 这次压缩至少要省下阈值的这个比例, 否则不压 (2026-09-23): 花一次摘要调用只换回
# 几百 token 不划算. 用**比例**而不是绝对值: 它是「值不值得花一次钱」的相对门槛,
# 跟着阈值走才不用两处一起调 (Anthropic 的 `clear_at_least` 也是配在 trigger 旁边).
DEFAULT_CLEAR_AT_LEAST_RATIO = 0.1
# 摘要那一次调用的**输入**上界 (2026-09-23): 材料再多也只喂这么多. 被裁的那段可能
# 比它要省的还大 —— 没有上界的话, 为了省 token 先花一大笔.
DEFAULT_SUMMARY_INPUT_LIMIT = 8_000


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
        reasoning_cleared: 这次清了几条思维链 (只在 `reasoning_keep_turns` 开着时
            可能非 0).
        estimated_tokens: 压后估算 (这份视图作为一次请求大概多大).
        saved_tokens: 省下的估算量 (同一把尺子量的账本与视图之差; 压后反而更大
            时为负 —— 如实报, 不夹到 0).
        summarized: 这次走没走摘要 (False = 只做了裁剪).
        summarizer_usage: 摘要那一次调用的 usage (要计入本次运行预算, 但不占
            max_turns 的额度 —— 它不是一次模型决策).
        warning: 降级原因 (没有摘要模型 / 摘要失败); None 表示这次没出岔子.
        skipped: 这一轮**为什么没压** (诊断值, 机器读的短标识); None 表示没被挡.
            目前只有 `"clear_at_least"` 一种 (省得不够本). 与 warning 分开: 那个是
            「本来要做却没做成」, 这个是「算了, 不值当」—— 塞进 warning 会让前端在
            每一次「压不动」时都显示一句降级说明.
        emergency: 这一次是**紧急压缩** (上游回报输入超窗口之后的那一次, 见
            `CompactionPolicy.emergency`); False 表示平时的投影 / 裁剪.
    """

    messages: list[ModelMessage]
    summary: str | None = None
    summary_covers: int = 0
    dropped: int = 0
    truncated: int = 0
    reasoning_cleared: int = 0
    estimated_tokens: int = 0
    saved_tokens: int = 0
    summarized: bool = False
    summarizer_usage: Usage | None = None
    warning: str | None = None
    skipped: str | None = None
    emergency: bool = False

    @property
    def compacted(self) -> bool:
        """这次真的压了吗 —— 决定发不发 context_compacted 事件.

        判据是「有没有东西变了」而不是「试没试过」: 没超阈值、压不动 (只有一个
        提问)、或者省得不够本 (clear_at_least 挡下), 都不该在前端留一条「压缩过」
        的痕迹.
        """
        return self.dropped > 0 or self.truncated > 0 or self.reasoning_cleared > 0


def compiled_counts(compiled: CompiledView) -> dict[str, Any]:
    """这次压缩「做了什么」的九个键 (**唯一一处定义**).

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
        "reasoning_cleared": compiled.reasoning_cleared,
        "estimated_tokens": compiled.estimated_tokens,
        "saved_tokens": compiled.saved_tokens,
        "summarized": compiled.summarized,
        "warning": compiled.warning,
        "skipped": compiled.skipped,
        "emergency": compiled.emergency,
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


def note_request_diagnostics(view: dict[str, Any] | None, usage: Usage | None) -> None:
    """把这一轮**事后才知道**的两个诊断值补进视图载荷 (原地改; 没数据就不写键).

    - `estimate_drift`: 这一轮的估算 - 上游回报的 input_tokens. 压缩的触发与水位线
      全建立在估算上, 而「估得准不准」此前没有任何一处答得出来 —— 缺陷 1 (锚的坐标
      错位) 就是这么藏了很久的.
    - `cache_hit_ratio`: 命中 / (命中 + 未命中). 压缩会**换掉前缀**, 命中率因此往下
      掉 —— 这条曲线是「压缩花掉的钱」最直接的证据 (Manus 把 KV-cache 命中率当生产
      环境的第一个指标).

    两个都是**有就记、没有就不写这个键** (缺 usage / 缺分量时): 「没上报」与「确实
    是零」在诊断里是相反的结论, 写 0 就把后者说成了前者.

    Args:
        view: 这一轮的视图载荷 (`view_payload` 的产物); None = 没投影过, 跳过.
        usage: 这一轮响应的 usage; None = 上游没给.
    """
    if view is None or usage is None:
        return
    estimated = view.get("estimated_tokens")
    if usage.input_tokens is not None and isinstance(estimated, int):
        view["estimate_drift"] = estimated - usage.input_tokens
    hit, miss = usage.cache_hit_tokens, usage.cache_miss_tokens
    if hit is not None and miss is not None and hit + miss > 0:
        view["cache_hit_ratio"] = hit / (hit + miss)


class TokenCounter(Protocol):
    """SPI: 估算「这份消息列表作为一次请求, 上游会算多少输入 token」.

    两个方法都属于契约 (不是可选增强):

    - `count` 是纯查询, 策略拿它判阈值与水位线;
    - `note_usage` 是**权威值回灌** —— 上游的 usage 到了, 请更新你的校准. 真正
      权威的输入 token 数只有 API 给的 usage, 估算只用来判阈值; 真分词器
      (tiktoken / 官方 tokenizer) 不需要校准, 把这一条写成空实现即可. 框架刻意
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

    def note_usage(self, usage: Usage | None, *, sent: Sequence[ModelMessage]) -> None:
        """上游给了这一次调用的真实用量, 更新估算的校准.

        Args:
            usage: 这一次响应的 usage; None 或没有 input_tokens 时校准不动
                (没有权威值就继续用上一次那个).
            sent: 这一次**真发出去的那份列表** —— 权威值对应的就是它. 拿别的列表
                来对会得到一个错的校准 (2026-09-23: 从前记的是「账本有几条」, 而
                发出去的是视图, 压缩过之后两者不等, 于是同一份列表算出两个值).
        """
        ...


@dataclass(slots=True)
class CalibratedTokenCounter:
    """默认估算器: 字符启发式 + 一个从上游真实用量反推出来的固定开销.

    为什么用「校准」而不是「锚 + 增量」(2026-09-23 改): 锚式要求「量的一定是账本
    那条 append-only 线的延伸」, 而压缩之后送出去的有时候是**视图** —— 两份列表的
    坐标对不上, 于是同一份列表能算出两个差很远的值 (复现: 锚 = 8000 时, 同一份
    视图量出 415, 而账本被量成 13291 —— 账本自己的启发式只有 6520). 校准不做这个
    假设: 一份列表多大, 就是它的启发式加上那个常量, 谁来问都一样.

    常量 = 真实 input_tokens - 那份列表的启发式估算. 它主要装着启发式**看不见**的
    固定开销 (工具 schema / 系统提示的模板部分), 比例型偏差 (中文实际 token/字 与
    「一字一 token」的差) 也会被它吸收一部分, 所以它随列表大小轻微漂移 —— 换来的是
    **任何一份列表都走同一条算式**, 而不是「有时精确、有时错得很远」.

    代价: 估算不再随真实用量自校准 (锚式在「列表确实是账本延伸」时更准). 取舍见
    ADR-0011.
    """

    _overhead: int = field(default=0, init=False, repr=False)

    def note_usage(self, usage: Usage | None, *, sent: Sequence[ModelMessage]) -> None:
        """记下校准项 (没有 input_tokens 就什么都不做, 见 TokenCounter)."""
        if usage is None or usage.input_tokens is None:
            return
        self._overhead = usage.input_tokens - estimate_tokens(sent)

    def count(self, messages: Sequence[ModelMessage]) -> int:
        """这份列表的估算: 启发式 + 校准项 (还没校准过时校准项就是 0).

        校准项可以是**负的** (启发式高估时): 如实调, 不夹到 0 —— 夹了等于假装
        「启发式从不高估」, 于是估算永远偏大, 压缩会压得比该压的更勤.
        """
        return estimate_tokens(messages) + self._overhead


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

    async def emergency(
        self,
        history: Sequence[ModelMessage],
        *,
        summary: str | None,
        summary_covers: int,
        counter: TokenCounter,
        summarizer: ChatModel | None = None,
    ) -> CompiledView:
        """**紧急压缩** (上游说这份输入超了窗口时才走): 尽力裁到还能发出去.

        与 `apply` 的区别是它**不设门槛**: 不问阈值、不问水位线、摘要失败也照裁 ——
        触发它的前提本身就是「不裁就发不出去」, 于是它保的是**这一次请求还能不能
        成立**, 而不是信息完整. 实现照旧**不得改动传进来的 history**.

        Args:
            history / summary / summary_covers / counter / summarizer: 同 `apply`.

        Returns:
            CompiledView: 视图 + 新进度 (`emergency=True`). 连一刀都裁不动时返回原样
            —— 让上层照旧失败, 不假装成功.
        """
        ...


@dataclass(slots=True)
class TrimAndSummarize:
    """默认策略: 窗口裁剪 + 工具结果截断 + 滚动摘要 + 老思维链清理 (difficulties #7).

    四件里前三件默认开 (见模块 docstring 的清单), 第四件 (思维链清理) 默认**关** ——
    DeepSeek 那条「带 tools 时历史 reasoning 须完整回传」的契约没被证伪.

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
        clear_at_least_ratio: 这次压缩至少要省下阈值的这个比例, 否则不压 (0 表示
            不设这道闸). 它是「值不值得花一次摘要调用」的门槛, 所以用比例表达.
        summary_input_limit: 摘要那一次调用的**输入**上界 (token); 材料超了就砍掉
            最旧的那端. **0 表示不截** (要关掉这道上界就填 0).
        reasoning_keep_turns: 保留最近几轮的思维链, 更早的换成占位文本; None
            (默认) 表示**不清理** —— DeepSeek 要求历史 reasoning 完整回传, 那条契约
            没被证伪, 所以默认关 (要省这份空间才开).
    """

    threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS
    keep_recent_questions: int = DEFAULT_KEEP_RECENT_QUESTIONS
    watermark_ratio: float = DEFAULT_WATERMARK_RATIO
    tool_result_limit: int = DEFAULT_TOOL_RESULT_LIMIT
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS
    clear_at_least_ratio: float = DEFAULT_CLEAR_AT_LEAST_RATIO
    summary_input_limit: int = DEFAULT_SUMMARY_INPUT_LIMIT
    reasoning_keep_turns: int | None = None
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
        if not 0 <= self.clear_at_least_ratio < 1:
            raise CompactionConfigError(
                f"clear_at_least_ratio 必须在 [0, 1) 之间 (它是阈值的比例, 0 = 不设"
                f"闸), 实际: {self.clear_at_least_ratio}"
            )
        if self.summary_input_limit < 0:
            raise CompactionConfigError(
                f"summary_input_limit 必须 >= 0 (0 = 不截), 实际: "
                f"{self.summary_input_limit}"
            )
        if self.reasoning_keep_turns is not None and self.reasoning_keep_turns < 1:
            raise CompactionConfigError(
                f"reasoning_keep_turns 必须 >= 1 (None = 不清理), 实际: "
                f"{self.reasoning_keep_turns}"
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
        # 投影 (每轮都做): 摘要替换掉它覆盖的那一段, 老工具结果**不截** (截断与裁剪
        # 同属「再切一刀」那一半, 见 _view 的 truncate_tools)
        projected, _, _ = self._view(
            history,
            cut=max(summary_covers, 1),
            summary=summary,
            latest_start=_latest_question_start(history),
            trim_old_content=False,
        )
        # 判阈值量的是**这份投影**, 不是账本 (2026-09-23 改): 账本是 append-only 的,
        # 只增不减 —— 拿它判的话压完一次就永远超线, 于是每轮都切一刀、每轮都烧一次
        # 摘要. 投影才是真正要发出去的东西, 它因为「上一刀已经推过切点」而变小,
        # **滞回**就是这么来的 (从前靠「锚落到小值」提供, 而那个锚本身会错位).
        before = counter.count(projected)
        unchanged = CompiledView(
            messages=projected,
            summary=summary,
            summary_covers=summary_covers,
            # 没切刀: dropped / truncated / summarized 都保持「什么都没做」,
            # 于是不发 context_compacted 事件 (上一次压缩已经报过一次了);
            # 但它确实比账本小 —— saved_tokens 如实报
            estimated_tokens=before,
            saved_tokens=estimate_tokens(history) - estimate_tokens(projected),
        )
        if before < self.threshold_tokens:
            return unchanged

        cut = self._choose_cut(history, summary=summary, counter=counter)
        if not cut:
            # 压不动 (只有一个提问 / 没有可切的提问): 如实报告什么都没做
            return unchanged

        # clear_at_least (2026-09-23): 这一刀至少要省下阈值的那个比例, 否则不压 ——
        # 花一次摘要调用只换回几百 token 不划算. **基线是这一轮的投影** (不切就会发
        # 出去的那份), 不是账本: 账本里含着上一次已经裁掉的段, 拿它当基线会把那些段
        # **重复计**成这一次的节省 —— 压过一次之后这道门就形同虚设. 两把尺子都走
        # counter, 口径一致.
        trial, _, _ = self._view(
            history,
            cut=cut,
            summary=summary,
            latest_start=_latest_question_start(history),
            summary_slot=True,
        )
        saving = counter.count(projected) - counter.count(trial)
        # 两道门, 缺一不可:
        # ① 省下来得**是正的** —— 压完反而更大就别压 (视图里会多出摘要那条, 账本小
        #    的时候它可能比裁掉的那段还大), 这一道与配置无关;
        # ② 还得**够本** (clear_at_least_ratio; 0 表示只留第 ① 道).
        # Note: 试算里那个摘要槽是空的或上一条 (见 `_view` 的 summary_slot), 新摘要
        # 自身占的那点地方没算进去 —— 于是这两道门**偏乐观**. 量级上它最多是
        # summary_max_tokens, 相对阈值可以忽略; 真要保守就调高 clear_at_least_ratio.
        if saving <= 0 or saving < int(
            self.threshold_tokens * self.clear_at_least_ratio
        ):
            return replace(unchanged, skipped="clear_at_least")

        material_start = max(summary_covers, 1)
        materials: list[ModelMessage] = []
        trimmed = 0
        if self.summarize:
            window = history[material_start:cut]
            materials = cap_summary_material(
                window,
                limit=self.summary_input_limit,
                tool_limit=self.tool_result_limit,
            )
            trimmed = len(window) - len(materials)
        asked_for_summary = self.summarize and bool(materials)
        if asked_for_summary:
            new_summary, usage, warning = await self._summarize(
                materials, previous=summary, model=summarizer
            )
        else:
            # 两种情形共用这一支, 含义不同:
            # - 配置关掉摘要: 连一次调用都不发, 也不留降级原因 (见 CompiledView.warning)
            # - 摘要开着但**没有新材料** (切点没往前推): 那同样是「纯裁剪」, 不是失败
            #   —— 按失败处理的话, covers == cut 这个稳态会让视图永远超阈值
            new_summary, usage, warning = None, None, None
        summarized = new_summary is not None
        if asked_for_summary and not summarized:
            # 摘要失败 → **本轮不切刀**, 退到「只投影」那一步 (2026-09-23 审计).
            # 切点一旦推进, [covers, cut) 那段的原文就既不在摘要里也不在视图里 ——
            # 凭空消失. 复现: 账本 17 条时 dropped=14 而 covers=0, 且下一轮接着推,
            # 于是每轮只看得见最后一个问答、永远没有摘要. 「允许丢信息」的权力留给
            # emergency —— 它只在真超限时才用.
            # 留痕两处都带上: 降级原因 + **那一次已经花掉的**用量 (它确实计费了)
            return replace(unchanged, warning=warning, summarizer_usage=usage)
        if trimmed:
            # 被输入上界削掉的那些**没进过摘要**, 而切点照旧推进 —— 它们就此丢了.
            # 这是 `summary_input_limit` 的**代价** (配置驱动, 与 `summarize=False`
            # 的硬截断同一类), 但**不能静默**: 摘要成没成都如实说一声.
            # 为什么不把切点退回去让它们留在视图里: 那会让下一轮的投影把**已经进过
            # 摘要**的那些段又拉回来 (covers 与切点分了家), 视图于是在相邻两轮间跳变
            # —— 正是 `_choose_cut` 那条不变量要挡住的东西.
            warning = (
                f"摘要材料超过输入上界 ({self.summary_input_limit} token), "
                f"最旧的 {trimmed} 条没进摘要"
            )
        final_summary = new_summary if summarized else summary
        view, truncated, cleared = self._view(
            history,
            cut=cut,
            summary=final_summary,
            latest_start=_latest_question_start(history),
        )
        return CompiledView(
            messages=view,
            summary=final_summary,
            # 走到这里只有两种情形: 摘要成了 (covers 与切点同进退), 或者摘要被配置
            # 关掉 (那时硬截断是**用户选的**, covers 跟着推进才算账目自洽)
            summary_covers=cut,
            dropped=cut - 1,
            truncated=truncated,
            reasoning_cleared=cleared,
            estimated_tokens=counter.count(view),
            saved_tokens=estimate_tokens(history) - estimate_tokens(view),
            summarized=summarized,
            summarizer_usage=usage,
            warning=warning,
        )

    async def emergency(
        self,
        history: Sequence[ModelMessage],
        *,
        summary: str | None,
        summary_covers: int,
        counter: TokenCounter,
        summarizer: ChatModel | None = None,
    ) -> CompiledView:
        """**紧急压缩**: 不问阈值也不问水位线, 能裁多狠裁多狠.

        与 `apply` 的区别只有「不留情面」, 两处:

        - 切点直接取「只剩最近 1 个提问」(`_cut_index(history, 1)`), 不看水位线;
        - **摘要失败也照裁** —— 平时那条「摘要失败就不切刀」的纪律保护的是**信息**,
          而这里保的是**这一次请求还能不能发出去**;
        - **正在回答的那段也截** (`latest_start=len(history)`) —— 平时那条「最新那个
          提问的内容不截」让位给「先发得出去」. 这一条不是可有可无的: 紧急路径的
          典型触发就是「这一轮刚取回一个大工具结果」, 而那段正文正是保留段的全部
          (`cut` 与 `latest_start` 在只剩 1 个提问时相等), 不截的话等于什么都没做.

        何时走这里: 上游明确回报「输入超窗口」时 (见 `ModelErrorKind.CONTEXT_OVERFLOW`).
        代价记在账目上: `summary_covers` 推进到切点, 也就是把 `[covers, cut)` 那一段
        **显式声明为「有意放弃」**(既不在摘要里, 也不在视图里) —— 这是本条路径上
        「允许丢信息」的唯一凭据.

        Returns:
            CompiledView: 视图 + 新进度 (`emergency=True`). 连一刀都裁不动时返回原样
            —— 让上层照旧失败, 不假装成功.
        """
        cut = _cut_index(history, 1)
        if not cut:
            return CompiledView(
                messages=list(history),
                summary=summary,
                summary_covers=summary_covers,
                estimated_tokens=counter.count(history),
                emergency=True,
            )
        if self.summarize:
            new_summary, usage, warning = await self._summarize(
                cap_summary_material(
                    history[max(summary_covers, 1) : cut],
                    limit=self.summary_input_limit,
                    tool_limit=self.tool_result_limit,
                ),
                previous=summary,
                model=summarizer,
            )
        else:
            new_summary, usage, warning = None, None, None
        summarized = new_summary is not None
        final_summary = new_summary if summarized else summary
        view, truncated, cleared = self._view(
            history,
            cut=cut,
            summary=final_summary,
            # 保护解除: 保留段里的老工具结果也截、老思维链也清 (见 docstring)
            latest_start=len(history),
        )
        return CompiledView(
            messages=view,
            summary=final_summary,
            summary_covers=cut,
            dropped=cut - 1,
            truncated=truncated,
            reasoning_cleared=cleared,
            estimated_tokens=counter.count(view),
            saved_tokens=estimate_tokens(history) - estimate_tokens(view),
            summarized=summarized,
            summarizer_usage=usage,
            warning=warning,
            emergency=True,
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
            trial, _, _ = self._view(
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
        trim_old_content: bool = True,
    ) -> tuple[list[ModelMessage], int, int]:
        """切出视图: 第 0 条 + (摘要) + 第 cut 条起, 老正文截短 / 老思维链清掉.

        Args:
            summary: 放进视图的摘要正文.
            summary_slot: 没有摘要时也占一个空位 —— 给**试算**用. 压完摘要那条
                消息一定会在位 (它是「被裁掉那段」的唯一记录), 试算时漏掉它,
                水位线就会卡在边上 (压完刚好又超一点).
            trim_old_content: 要不要顺手把老正文变小 (工具结果截短 + 思维链清成占位).
                **投影那一路给 False** —— 这两种都是「再切一刀」那一半的手段 (与裁剪
                同进退), 而**投影每一轮都做** (见 apply): 不切刀的那一轮只把摘要放
                回去, 不该顺手改正文 (2026-09-23, ticket 23).

        Returns:
            tuple: (视图消息, 截短了几条工具结果, 清了几条思维链).
        """
        view: list[ModelMessage] = [history[0]]
        if summary or summary_slot:
            view.append(summary_view_message(summary or ""))
        boundary = (
            _boundary_from_the_end(history, self.reasoning_keep_turns)
            if self.reasoning_keep_turns is not None
            else 0
        )
        truncated = 0
        cleared = 0
        for index in range(cut, len(history)):
            message = history[index]
            content = message.get("content")
            if (
                trim_old_content
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
            if (
                trim_old_content
                and index < boundary
                and isinstance(message.get("reasoning_content"), str)
            ):
                # 老那几轮的思维链换成占位 (不删 key —— wire 形状稳定)
                message = {**message, "reasoning_content": REASONING_CLEARED_TEXT}
                cleared += 1
            view.append(message)
        return view, truncated, cleared

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
            logger.warning("没有可用的摘要模型, 本轮不裁剪 (只投影)")
            return None, None, "没有可用的摘要模型, 本轮不裁剪"
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
                # effort 也要显式压到 "none": 调用级不传时适配器会回落到**实例默认**
                # 的强度, 而它与 thinking=False 方向相反 —— check_thinking_params 会
                # fail fast 抛错, 被下面那个 except 吞成一条 warning, 于是摘要永远
                # 停在只投影 (与 2026-09-23 那次漏掉 thinking 是同一个坑的两个面)
                reasoning_effort=THINKING_OFF_EFFORT,
            )
        except Exception as exc:
            # 摘要只是「压得更好看」, 不是必需件: 它失败不该让用户这一句问不出来
            # (降级为「只投影、不切刀」; 留痕两处: 日志 + 视图上的 warning). 刻意
            # 不吞 CancelledError —— 它继承 BaseException, kill switch 照样打得断.
            logger.warning("摘要生成失败, 本轮不裁剪: %r", exc)
            reason = f"摘要没能生成 ({type(exc).__name__}), 本轮不裁剪"
            return None, None, reason
        text = (response.content or "").strip()
        if not text:
            logger.warning("摘要模型没给出正文, 本轮不裁剪")
            return None, response.usage, "摘要模型没有给出正文, 本轮不裁剪"
        if response.finish_reason is FinishReason.LENGTH:
            # 撞了 max_tokens: 手里这份是半截. **不当摘要用** —— 摘要随对话滚动
            # 增长, 一旦开始撞上限, 往后每一轮都是半截, 而下一轮还拿它当 previous
            # 继续重压, 信息逐次损失且没有任何告警 (2026-09-23 审计). 与「没吐
            # 正文」同一条出口: 不切刀, 下次再试.
            logger.warning("摘要被长度限制截断 (finish_reason=length), 本轮不裁剪")
            return None, response.usage, "摘要被长度限制截断, 本轮不裁剪"
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


def _boundary_from_the_end(history: Sequence[ModelMessage], keep: int) -> int:
    """倒着数 keep 个提问, 那个提问的下标 (思维链的清理界: 它之前的老轮清掉).

    与 `_cut_index` 分开: 那个还要保证「切完不是只剩 system」「被裁段里有 assistant」
    —— 那些是**裁剪**的安全条件. 清理思维链不移动任何消息, 于是只需要一个位置.

    Returns:
        int: 边界下标; 0 表示提问不够 keep 个 (没有可清的界 —— 它们都算「最近」).
    """
    starts = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(starts) < keep:
        return 0
    return starts[-keep]


def _latest_question_start(history: Sequence[ModelMessage]) -> int:
    """正在回答的那个提问的下标 (最后一个 user 消息的位置); 没有提问时返回 len(history).

    用处只有一个: 「正在回答的那段不截」—— 模型正拿着这份工具结果答这一句,
    截了就是答非所问. 返回 len(history) 表示没有受保护的那段 (全都可以截).
    """
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            return index
    return len(history)
