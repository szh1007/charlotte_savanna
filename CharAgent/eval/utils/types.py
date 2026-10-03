"""eval 包的词汇: 一道题 / 一次跑交出的事实 / 一个判据给的结论, 外加一张映射表.

一句话理解: 这个文件定义**跑分时大家在说的那几个词** —— 不跑、不渲染, 只把形状
定死. 行为在 `runner.py` (怎么跑) 与 `report.py` (怎么显示).

五个词:

| 词 | 一句话 |
|----|--------|
| `EvalCase` | 一道题: 题面 + 期望工具集 + (可选) 期望参数 + 元信息 |
| `CallRecord` | 一次工具调用的事实: 名字 / 参数原文 / 状态 / 耗时 |
| `RunFacts` | 一跑交出来的全部事实: 上面那串调用 + 回答 + 终局 + 成本规模 |
| `RunOutcome` | 这一跑的终局**算不算数** (四分类, 见下) |
| `Metric` / `Judgment` | 一个判据给的可汇总子指标与结论 |

**`RunOutcome` 为什么是四分类而不是「对 / 错」** (L4 规划期定, 本包最要紧的一条):
全挂工具集那组的 prompt 前缀更长, 更容易撞上 token 预算. 若把「没跑完」算作
「答错」, 那一组的分数会被前缀长度压低 —— 而那个差异**不是选择能力带来的**,
拿它当结论就是错的. 于是终局先与判据分开: 只有 `COMPLETED` 的跑次进判据的分母,
另外三种**如实计数、单列**, 绝不算作答错.

四个取值各自的成因:

| 取值 | 什么情况 | 算不算数 |
|------|---------|---------|
| `COMPLETED` | 模型给出了最终答复 (自然收尾) | 算 |
| `TRUNCATED` | 轮数 / token / 时长用尽, 或反复截断放弃 —— 没答完 | 不算 |
| `SUSPENDED` | 停在挂起点等人给结论 (#25 HITL) | 不算 |
| `BROKEN` | 上游把生成打断了, 工具超时中断 (#15), 或这一跑直接抛了错 | 不算 |

第四类 (`BROKEN`) 是本包相对票据多出的一档, 理由: 上游抖一下与判据里那个「截断」
是**两回事**, 混在一起会让「裁剪之后截断少了多少」这个数字被上游抖动污染; 而
「跑批途中某个 subject 装配失败」也总得有个去处 —— 它既不是答错也不能算跑完.

**「不算数」不等于「不记」**: 三种都逐条进报告 (逐题表 + 三分类分布 + 失败样本),
只是不进判据的分子分母.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from CharAgent.agent.utils.types import LoopOutcome
from CharAgent.db.entities import ToolCallStatus

# ---------------------------------------------------------------------------
# 一道题
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalCase:
    """一道跑分题 (业务从 YAML 加载, 见 issue 42).

    attributes:
        id: 题号 (报告里逐题表的行标签, 要求**唯一**且短 —— 差异归因那张表只写
            题号, 靠它回查).
        question: 题面 (真正喂给会话的那句话).
        expect_tools: 期望工具集 (**集合**, 不是序列). 它是「这道题该调哪几个
            工具」的答案, 召回率的分母; 顺序与重复都不影响判据 —— 同一个工具调
            两次算一次命中 (L4 规划期定: 按集合去重).
            空元组 = **这道题本来就不该调工具** (纯闲聊 / 越界提问), 它在报告里
            单列 (零期望样本), 因为「一个都没调」对它是满分而不是漏调.
        expect_args: 期望参数 (**可选**): 工具名 -> 参数子集. 只写要断的那几个键,
            没写的键不参与判定 (与 `trace_assertions` 的子集匹配同一条口径).
        meta: 元信息 (场景分类 / 备注 / 期望结局 …), 框架**只看不解释** —— 与
            `RunContext.payload`、`Tool.annotations` 同一条纪律. 报告把它原样
            署名进 JSON, 判据想读也能读.
    """

    id: str
    question: str
    expect_tools: tuple[str, ...] = ()
    expect_args: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 一跑的终局
# ---------------------------------------------------------------------------


class RunOutcome(StrEnum):
    """一跑的终局: 这一跑**算不算数** (不是「对 / 错」, 见模块 docstring).

    四个取值的成因与处置:

    - `COMPLETED`: 模型给出了最终答复. 只有它进判据的分母.
    - `TRUNCATED`: 轮数 / token / 时长任一项用尽, 或反复 length 截断后放弃 ——
      **没答完, 不是答错**. 单列计数 (L4 的两组对比里它本身就是一条收益指标).
    - `SUSPENDED`: 停在挂起点等人 (#25 HITL). 单列; L4 里由 issue 43 那批题
      单独走恢复流程.
    - `BROKEN`: 上游打断了生成, 或这一跑直接抛了错. 单列 —— 它绝不该被算进
      「截断」, 否则上游抖动会污染截断那个数字.
    """

    COMPLETED = "completed"
    TRUNCATED = "truncated"
    SUSPENDED = "suspended"
    BROKEN = "broken"


# ---------------------------------------------------------------------------
# 一次跑交出的事实
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallRecord:
    """一次工具调用的事实 (判据看的就是它).

    字段与 `ToolCallFact` (agent 侧) / `ToolCall` (db 行) 是同一组概念, 刻意不复用
    那两个类型: 本包是**离线读一份事实**, 不关心它是从 loop 的当轮记录来的还是从
    记录层按 run_id 读回来的 —— 业务侧两条路都能建出它 (L4 用的是后一条).

    attributes:
        tool_name: 工具名.
        arguments: 模型填的**原样 JSON 字符串** (不预解析: 畸形 JSON 正是自纠错
            路径的信号, 判据自己决定怎么读它).
        status: 这一条最后的落点 (`ToolCallStatus`). 判据多半只看成功与失败两类,
            挂起那一条 (`NEEDS_APPROVAL`) 是「当前这一跑停在这儿」的旁证.
        duration_ms: 执行耗时 (毫秒); None = 没跑到计时那一步 (与 0 毫秒是两回事).
    """

    tool_name: str
    arguments: str = ""
    status: ToolCallStatus = ToolCallStatus.SUCCEEDED
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RunFacts:
    """一跑交出来的全部事实 (`EvalSubject.run_once` 的返回值, 判据的输入).

    它是**判据与报告唯一的数据来源** —— `Judge` 只拿到 `(case, facts)`, 不看会话、
    不看库、不碰网络. 于是判据是纯函数, 跑分可离线重判.

    attributes:
        tool_calls: 这一跑的工具调用, **按发生序**摊平 (含同一轮的并行调用, 顺序即
            那一轮响应内的顺序). 空元组 = 一个工具都没调.
        answer: 最终答复正文; None = 这一跑没给出答复 (截断 / 挂起 / 坏了 —— 与
            「答复是空串」是两回事).
        outcome: 这一跑的终局, 见 `RunOutcome`.
        error: 为什么是这个终局 (一般填 `LoopResult.outcome` 的值, 如
            `token_budget`); 正常跑完是空串. 业务侧自己抛的错也可以把一句话放这里.
        turns: 模型决策次数 (轮数).
        tokens: 这一次跑的累计 token.
        elapsed_ms: 墙钟耗时 (毫秒).
        run_id: 运行编号 (回看与归因的钥匙); None = 这一跑没走记录层.
        cost: 这次运行折算的金额 (**元**, 与 `runs.total_cost` 同一标度); None =
            没算 (没配价目表 / 记录层没写) —— 不是 0.
        config: 这一批跑的配置快照 (模型名 / prompt 版本 / 工具集大小 / 三道刹车 /
            压缩旋钮 …). 报告头部那块直接渲染它, **键由业务定** —— 框架只负责原样
            摆出来, 不解释哪个键是什么意思 (与 `meta` 同一条纪律).

            为什么放在每次跑的事实里而不是当跑批器的参数: 只有装配会话的那一方
            知道这一跑到底用的什么参数, 让它自己说才不会与实际跑的东西漂开.
            同一批里各跑次**应当**给同一份; 真给了不一样的, 报告会如实点名 (见
            `report.py` 的配置快照那块).
    """

    tool_calls: tuple[CallRecord, ...] = ()
    answer: str | None = None
    outcome: RunOutcome = RunOutcome.COMPLETED
    error: str = ""
    turns: int = 0
    tokens: int = 0
    elapsed_ms: float = 0.0
    run_id: str | None = None
    cost: float | None = None
    config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def tool_names(self) -> tuple[str, ...]:
        """调过的工具名 (按发生序, 含重复) —— 报告里那一列与告警文案用."""
        return tuple(call.tool_name for call in self.tool_calls)

    @property
    def counted(self) -> bool:
        """这一跑进不进判据的分子分母 (只有 `COMPLETED` 进)."""
        return self.outcome is RunOutcome.COMPLETED


# agent loop 的结束原因 → 跑分的四分类.
#
# 与 `db/state.py` 的 `RUN_STATUS_FOR_OUTCOME` 同一个做法: 两层的耦合只在这一张表
# 里, `agent/` 与 `eval/` 不互相 import 业务逻辑. 注意两张表的**口径不同**, 不是
# 抄错: 数据层关心「这一行该记什么状态」(guard 刹车算 finished), 跑分层关心
# 「这一跑算不算数」(guard 刹车不算数) —— 同一个 LoopOutcome 落两处分道扬镳.
RUN_OUTCOME_FOR_LOOP: dict[LoopOutcome, RunOutcome] = {
    LoopOutcome.FINISHED: RunOutcome.COMPLETED,
    # 三道刹车 + 反复截断: 都是「没答完」. 混进判据会让长前缀那一组凭白吃亏
    LoopOutcome.MAX_TURNS: RunOutcome.TRUNCATED,
    LoopOutcome.TOKEN_BUDGET: RunOutcome.TRUNCATED,
    LoopOutcome.TIME_LIMIT: RunOutcome.TRUNCATED,
    LoopOutcome.TRUNCATION_LIMIT: RunOutcome.TRUNCATED,
    LoopOutcome.SUSPENDED: RunOutcome.SUSPENDED,
    LoopOutcome.SERVER_INTERRUPTED: RunOutcome.BROKEN,
    # 工具超时中断 (#15): 也没答完, 而且是「结果未知」那一类 —— 与上游中断同归
    # BROKEN (`.get` 的兜底本来就是它, 写出来是为了让这张表读得出全部成因)
    LoopOutcome.INTERRUPTED: RunOutcome.BROKEN,
}


def run_outcome_of(outcome: LoopOutcome) -> RunOutcome:
    """agent loop 的结束原因 → 跑分的四分类.

    Args:
        outcome: `LoopResult.outcome`.

    Returns:
        RunOutcome: 对应的终局; 表里没有的取值一律归 `BROKEN` (新加的 LoopOutcome
            在跑分层默认「不作数」—— 默认成算数会把还没想清楚的东西悄悄计进分数).
    """
    return RUN_OUTCOME_FOR_LOOP.get(outcome, RunOutcome.BROKEN)


# ---------------------------------------------------------------------------
# 一个判据给的结论
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Metric:
    """一个**可汇总**的子指标: 分子 + 分母.

    **为什么不是直接给一个比率**: L4 的口径是「跨样本把分子分母分别相加再相除」
    (pooled), 不是「各个样本的比率求平均」. 后者会让「只该调 1 个工具的题」与
    「该调 3 个工具的题」权重相同, 而召回率的定义不是那样. 比率一旦提前算掉就
    汇不回来了 —— 所以交出来的是这一对.

    attributes:
        hit: 命中的量 (召回率这条题里就是「期望工具里调到了几个」).
        total: 这一跑该算的分母 (那道题期望工具的总数). 0 表示**这道题在这个指标
            上没有分母** (零期望样本), 它在汇总里被排除而不是按 0 分算.
    """

    hit: float
    total: float = 1.0

    @property
    def rate(self) -> float | None:
        """这一跑在这个指标上的比率; `total` 为 0 时是 None (不是 0).

        「没有分母」与「比率为零」在报告里必须分得开: 前者是这道题不适用, 后者是
        真的一分没得.
        """
        return None if self.total == 0 else self.hit / self.total


@dataclass(frozen=True, slots=True)
class Judgment:
    """一个判据对一次运行的结论.

    attributes:
        ok: 这次运行在这个判据上过了没有 (**这一条的**结论, 不是整批的分数).
        reason: 一句话说清「差在哪」; 过了就可以是空串. 失败样本那张表直接用它.
        metrics: 可汇总的子指标: 名字 -> `Metric`. 名相同的一组会跨样本池化, 于是
            汇总表里的比率是「分子之和 / 分母之和」而不是比率的平均 (见 `Metric`).
            判据不需要汇总就留空 —— `ok` 那一路照样计数.

    一个判据**能带多个子指标** (票据的开放决策 #5): 比如「工具选择」这一个判据
    同时交 `召回率` 与 `准确率` 两条, 它们各有各的分子分母 —— 合成一条就没法分别
    池化了.
    """

    ok: bool
    reason: str = ""
    metrics: Mapping[str, Metric] = field(default_factory=dict)
