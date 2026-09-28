"""跑批器: 把一批题按 N 次跑完, 每条都判一遍, 收成一份报告.

一句话理解: **框架管流程** —— for 组 / for 题 / for 第几跑, 每次现造一个被测对象、
问一道题、拿一份事实、叫所有判据各判一遍, 然后交给 `report.py` 排版. 业务管两件事
(怎么造对象、怎么判), 两者由 `protocols.py` 的两张协议接上.

四条跑批纪律 (都是 L4 规划期定死的):

1. **串行跑** (票据开放决策 #2). 20 题 x 3 次在假端点下是分钟级, 加并发带来的只是
   「这会儿哪几道题在飞」的归因麻烦 —— 真慢了再说.
2. **每一跑现造一个对象** (`build_subject(case)`), 跑完就关. 共用会话会让上一题的
   工具调用留在历史里, 下一题看到的上下文就不一样了, 轨迹串起来之后召回率算的是
   一锅粥.
3. **判据抛错不带走整批**: 记成 `judge_errors` 里的那一条继续跑. 一个判据在某道题
   上崩了, 不该让另外 59 跑的结论一起没了.
4. **开跑前自检**: 连第一个对象都装配不起来 (多半是缺 API key / 缺依赖) 就**当场
   抛**, 不静默跑到一半. 跑批器把「第一跑就失败」与「跑到第 40 跑才失败」分得很开:
   前者是环境没准备好 (去配环境), 后者是这一次跑的问题 (记成 `BROKEN` 接着跑).

**跑炸的那一跑也记**: `run_once` 抛出去、或第 N 跑的装配失败, 都由兜底记成一条
`outcome=BROKEN` 的跑次 (异常原文进 `error`). 凭空少掉几条跑次比多记一条坏数据
难查得多 —— 报告里「跑到一半少了几条」是没有痕迹的.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from CharAgent.eval.protocols import EvalSubject, Judge, SubjectFactory
from CharAgent.eval.utils.errors import EvalConfigError, EvalStartupError
from CharAgent.eval.utils.types import EvalCase, Judgment, RunFacts, RunOutcome

# 每题默认跑几次. 它决定「波动」那一块有没有意义, 所以**会写进报告头部** ——
# 常数在这里只是默认值, 真正的数从报告里读.
DEFAULT_TIMES = 3

# 最多几组. 对照是两两比 (A 对 B), 三组以上这张表表达不了 —— 与其含糊地只比前两
# 组, 不如当场说清.
MAX_GROUPS = 2


@dataclass(frozen=True, slots=True)
class EvalGroup:
    """一组跑分: 名字 + 造对象的工厂 + 这一组的题.

    组是 L4 两个 A/B 的落点 —— 「18 全挂」一组、「动态裁剪」一组, 两组跑同一批题,
    报告把两组并排摆出来.

    attributes:
        name: 组名 (报告里的列标签; 一个报告里不能重名).
        build_subject: 造被测对象的工厂, 每一跑调一次 (见模块 docstring 第 2 条).
        cases: 这一组的题. 两组的题**应当**是同一批 (不然逐题对照没有意义), 但
            本层不强制 —— 题集不同也可以各自跑分, 只是对照那块会少很多行.
    """

    name: str
    build_subject: SubjectFactory
    cases: Sequence[EvalCase]


@dataclass(frozen=True, slots=True)
class Attempt:
    """一次跑的全部产出: 事实 + 每个判据的结论.

    attributes:
        case: 这是哪道题的一跑.
        index: 第几跑 (1 起). 波动那一块按它切片.
        facts: 这一跑的事实.
        judgments: 判据名 -> 结论 (**只含没抛错的判据**).
        judge_errors: 判据名 -> 抛错原文. 与 `judgments` 一式两份、互斥: 一个判据
            在一次跑里要么给出结论要么抛错, 不会都有.

            刻意与「判为不过」分开: 判据崩了是**这次没判成** (汇总时跳过), 而
            `ok=False` 是**判成了但没过** (汇总时计入分母). 混成一个字段的后果是
            判据有个笔误, 报告上看起来像模型差了几分 —— 那是两种完全不同的修法.
    """

    case: EvalCase
    index: int
    facts: RunFacts
    judgments: Mapping[str, Judgment] = field(default_factory=dict)
    judge_errors: Mapping[str, str] = field(default_factory=dict)

    @property
    def judgeable(self) -> bool:
        """这一跑有没有判据的结论可用: 跑完了, 且至少一个判据没抛错.

        名字刻意不叫 `passed` / `passable` —— 它说的是「能判」而不是「判过了」:
        跑完且判过的跑次照样可能 `ok=False`. 混起来的后果是过滤失败样本时把
        「跑完但答错」和「压根没判成」当成同一件事.
        """
        return self.facts.counted and bool(self.judgments)


@dataclass(frozen=True, slots=True)
class GroupResult:
    """一组跑完之后的样子: 这一组的题 + 全部跑次 (按题号、跑次排好)."""

    name: str
    cases: tuple[EvalCase, ...]
    attempts: tuple[Attempt, ...]


@dataclass(frozen=True, slots=True)
class EvalReport:
    """一批跑分的全部产出: 时间 + 跑次设定 + 若干组.

    三份出口: `to_dict` (机器读的规范形状) / `to_json` (落盘给 `compare` 用) /
    `to_markdown` (人看的七块). Markdown 与 JSON **同源** —— 它渲染的就是 `to_dict`
    那份数据, 于是两者绝不会各说一套 (compare 读的也是同一份).
    """

    generated_at: datetime
    times: int
    judges: tuple[str, ...]
    groups: tuple[GroupResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """整份报告 → 可 json.dump 的字典 (落盘的形状, `compare` 读的就是它).

        Returns:
            dict: 顶层是 `schema_version` / `generated_at` / `times` / 每组一段.
        """
        from CharAgent.eval.report import report_to_dict

        return report_to_dict(self)

    def to_json(self, *, indent: int = 2) -> str:
        """整份报告 → JSON 文本 (中文不转义, 落盘之后人也能直接看)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_markdown(self) -> str:
        """整份报告 → Markdown (七块, 能直接贴进 README)."""
        from CharAgent.eval.report import render_markdown

        return render_markdown(self.to_dict())

    def group(self, name: str) -> GroupResult:
        """按名字取一组 (取不到就报错并列出有哪些).

        Raises:
            EvalConfigError: 没有这一组.
        """
        for item in self.groups:
            if item.name == name:
                return item
        raise EvalConfigError(
            f"报告里没有 {name!r} 这一组, 实际有: "
            f"{', '.join(group.name for group in self.groups) or '(一组都没有)'}"
        )


class EvalRunner:
    """跑批器: 收判据, 跑组, 交报告.

    判据表是**名字 -> 判据**: 名字就是报告里的列标签 (给报告用, 所以可以是中文),
    顺序就是报告的列序 (Python 的字典保序).
    """

    def __init__(self, *, judges: Mapping[str, Judge]) -> None:
        """装配跑批器.

        Args:
            judges: 判据名 -> 判据. **可以是空表** (只跑不判也成立: 想知道某一批题
                的轮数与 token 花销时用得上, 那时报告的判据列会是空的).
        """
        self._judges: dict[str, Judge] = dict(judges)

    @property
    def judges(self) -> tuple[str, ...]:
        """判据名 (报告的表头按这个顺序排)."""
        return tuple(self._judges)

    async def run(
        self,
        groups: Sequence[EvalGroup],
        *,
        times: int = DEFAULT_TIMES,
        now: datetime | None = None,
    ) -> EvalReport:
        """跑完这几组, 交报告.

        Args:
            groups: 一到两组 (见 `MAX_GROUPS`). 每一组自带对象工厂与题集.
            times: 每题跑几次. 是否写进报告头部由本方法的调用方决定 —— 它一定会
                被写进去 (波动那一块没有它就没有意义).
            now: 报告头部的跑分时间 (默认当下); 测试用它固定时间.

        Returns:
            EvalReport: 全部跑次与判据结论.

        Raises:
            EvalConfigError: 参数不合法 (跑次 < 1 / 一组都没有 / 超过两组 / 组名
                重复 / 某组没有题 / 一组的题号重复).
            EvalStartupError: 开跑前自检没过 (某一组的第一个对象装配不起来) ——
                错误里带上原始异常, 免得「缺 API key」这句话在中途被吃掉.
        """
        self._check(groups, times)
        # **每一组都要自检**: 两组各有各的对象工厂 (裁剪组与全挂组不是一个装配),
        # 只查第一组会让第二组的缺 key / 缺依赖留到跑到一半才以 60 条 BROKEN 现身
        for group in groups:
            await self._probe(group)

        generated = now if now is not None else datetime.now(UTC)
        results = tuple([await self._run_group(group, times) for group in groups])
        return EvalReport(
            generated_at=generated,
            times=times,
            judges=self.judges,
            groups=results,
        )

    # ------------------------------------------------------------------
    # 内部: 校验与自检
    # ------------------------------------------------------------------

    def _check(self, groups: Sequence[EvalGroup], times: int) -> None:
        """把参数错误在**开跑之前**全报出来 (跑了一半才发现要重来最亏).

        Raises:
            EvalConfigError: 见 `run`.
        """
        if times < 1:
            raise EvalConfigError(f"times 必须 >= 1, 实际: {times}")
        if not groups:
            raise EvalConfigError("一组都没有: 跑分至少要给一组 (组 = 对象工厂 + 题集)")
        if len(groups) > MAX_GROUPS:
            raise EvalConfigError(
                f"最多 {MAX_GROUPS} 组 (对照是两两比), 实际: {len(groups)} 组 "
                f"({', '.join(group.name for group in groups)})"
            )
        seen: list[str] = []
        for group in groups:
            if group.name in seen:
                raise EvalConfigError(f"组名重复: {group.name!r} (报告靠它区分两组)")
            seen.append(group.name)
            if not group.cases:
                raise EvalConfigError(f"组 {group.name!r} 一道题都没有")
            ids = [case.id for case in group.cases]
            duplicated = sorted({item for item in ids if ids.count(item) > 1})
            if duplicated:
                raise EvalConfigError(
                    f"组 {group.name!r} 的题号重复: {', '.join(duplicated)} "
                    "(题号是逐题表与差异归因的行标签, 必须唯一)"
                )

    async def _probe(self, group: EvalGroup) -> None:
        """开跑前自检: 这一组的第一个对象**造得出来吗** (造完就关, 不跑).

        为什么值得多造一个: 缺 API key 这类问题会让每一跑都以同样的方式失败 —— 没有
        自检的话, 报告出来是「60 跑全 BROKEN」, 而这件事本该在开跑前一句话说清.
        自检只验**装配** (不花 model 的钱): 缺 key / 缺依赖都在这一刻炸.

        Raises:
            EvalStartupError: 装配抛了任何异常 (原异常挂在 `__cause__` 上).
        """
        case = group.cases[0]
        try:
            subject = await group.build_subject(case)
        except Exception as exc:
            raise EvalStartupError(
                f"开跑前自检没过: 组 {group.name!r} 连第一个被测对象都装配不起来 "
                f"(多半是缺 API key 或依赖没装). 原始错误: {exc}"
            ) from exc
        # 关不上不打紧: 自检验的是**装得出来吗**, 而关不上的对象照样是装出来了
        await _close_quietly(subject)

    # ------------------------------------------------------------------
    # 内部: 跑一组
    # ------------------------------------------------------------------

    async def _run_group(self, group: EvalGroup, times: int) -> GroupResult:
        """串行跑完一组: for 题 -> for 第几跑 -> 造对象 / 问 / 判 / 关."""
        attempts: list[Attempt] = []
        for case in group.cases:
            for index in range(1, times + 1):
                attempts.append(await self._run_one(group, case, index))
        return GroupResult(
            name=group.name, cases=tuple(group.cases), attempts=tuple(attempts)
        )

    async def _run_one(self, group: EvalGroup, case: EvalCase, index: int) -> Attempt:
        """跑一次: 造对象 → 问 → 判 → 关 (关在 finally 里, 跑炸了也关).

        三条兜底都在这里: 装配失败 / `run_once` 抛错 → 记成 `BROKEN` 的一跑;
        判据抛错 → 记进 `judge_errors`. 三件事都不中断整批.
        """
        try:
            subject = await group.build_subject(case)
        except Exception as exc:
            # 自检已经保证「第一组的第一个」造得出来, 走到这里说明是某一次装配的
            # 偶发问题 (连接池满了 / 端口被占): 如实记一跑, 接着跑下一题
            return self._broken(case, index, f"装配失败: {exc}")
        try:
            facts = await subject.run_once(case)
        except Exception as exc:
            return self._broken(case, index, f"run_once 抛错: {exc}")
        finally:
            await _close_quietly(subject)
        judgments, errors = self._judge_all(case, facts)
        return Attempt(
            case=case,
            index=index,
            facts=facts,
            judgments=judgments,
            judge_errors=errors,
        )

    def _judge_all(
        self, case: EvalCase, facts: RunFacts
    ) -> tuple[dict[str, Judgment], dict[str, str]]:
        """叫每个判据各判一遍; 抛错的记下来, 不打断 (纪律第 3 条).

        Returns:
            tuple: (判据名 -> 结论, 判据名 -> 抛错原文), 两者互斥.
        """
        judgments: dict[str, Judgment] = {}
        errors: dict[str, str] = {}
        for name, judge in self._judges.items():
            try:
                judgments[name] = judge.judge(case, facts)
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
        return judgments, errors

    def _broken(self, case: EvalCase, index: int, error: str) -> Attempt:
        """一次跑炸了 → 一条 `BROKEN` 的跑次 (宁可多记一条坏数据, 不可凭空少一条)."""
        return Attempt(
            case=case,
            index=index,
            facts=RunFacts(outcome=RunOutcome.BROKEN, error=error),
        )


async def _close_quietly(subject: EvalSubject) -> None:
    """关掉被测对象; 关的时候炸了**不打紧** —— 这一跑的结论已经拿到了.

    关不上通常是连接池已经断了, 而那时该记的账 (轮数 / token / 工具轨迹) 都已经在
    `facts` 里. 让它抛出去会把一条已经跑完的记录变成一条 BROKEN, 那才是丢了真数据.
    """
    with contextlib.suppress(Exception):
        await subject.aclose()
