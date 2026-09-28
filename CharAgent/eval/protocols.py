"""eval 的两个接缝: 怎么造一个被测对象 (`EvalSubject`) 与怎么判 (`Judge`).

一句话理解: 框架管**流程** (跑 N 题 x M 次、收集、判分、落报告), 业务管**两件事**
—— 怎么造一个会答题的东西、怎么算它对不对. 这个文件就是把这两件事的形状定下来
的两张协议.

**为什么是协议而不是基类**: 与本仓其他接缝 (`ChatModel` / `ToolProvider` /
`TraceSink`) 同一条: 协议是 `Protocol`, 不要求继承 —— 业务侧那两类对象本来就
有自己的继承关系 (它们要继承业务自己的东西), 再塞一个框架基类进去只会把两边的
类型绑死.

**`EvalSubject` 为什么是「工厂造出来的一次性对象」**: 一次跑分要跑 N 题 x M 次,
每一次都必须是**干净的一跑** —— 换句话说是新会话、新记录层. 若让所有跑次共用一个
会话, 上一题的工具调用会留在历史里, 下一题看到的上下文就不一样了, 轨迹也就串了.
所以跑批器收的是**工厂**, 每一次跑都由它现造一个, 跑完就关掉:

    EvalGroup(name="全挂", build_subject=build_full, cases=[...])

工厂拿得到**这道题** (`make(case)`): 动态裁剪那组要按题面挑工具集, 而题面只有到
这一刻才知道 (issue 44). 用不上的组忽略这个参数即可.

**挂起 - 恢复留在业务侧**: 挂起那类题要「问一句 → 停下 → 补载荷 → 接着跑」, 这套
动作框架一点都不用知道 —— 它在业务侧的 `run_once` 里走完, 交回来的仍是一份
`RunFacts` (issue 43 就是这么做的).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from CharAgent.eval.utils.types import EvalCase, Judgment, RunFacts


@runtime_checkable
class EvalSubject(Protocol):
    """一个**被测对象的一次性实例**: 跑一道题, 交回事实.

    生命周期是「造 → `run_once` 一次 → 关」. `run_once` 只被调一次 —— 第二次调用
    该由工厂再造一个对象 (共用会话会让轨迹串, 见模块 docstring).
    """

    async def run_once(self, case: EvalCase) -> RunFacts:
        """把 `case` 问出去, 交回这一跑的全部事实.

        **异常也算事实**: 会话装配失败 / 工具端点炸了 / 上游超时, 都请如实填进
        `RunFacts` (`outcome=RunOutcome.BROKEN` + `error` 写清原因) 而不是抛出去
        —— 抛出去会让那一道题之后的整批仍在跑, 但报告里那 3 次跑次会凭空消失
        (跑批器兜底会把异常记成 `BROKEN`, 但兜底记下的只有异常原文, 拿不到这一跑
        已经产生的轮数与 token).

        Args:
            case: 这道题 (题面 + 期望 + 元信息).

        Returns:
            RunFacts: 这一跑的事实.
        """
        ...

    async def aclose(self) -> None:
        """关掉这一跑用的东西 (会话 / 模型客户端 / 记录层).

        跑批器在每一跑之后都调它, **包括跑炸了的那一跑** (在 finally 里). 漏关
        会让一批 60 跑攒下 60 个连接池.
        """
        ...


@runtime_checkable
class Judge(Protocol):
    """一个判据: 拿一道题与一跑的事实, 给一个结论.

    **纯函数**: 不碰网络、不碰库、不看时钟 —— 它只该依赖传进来的两个参数. 于是
    它便宜、可单测, 且同一份事实喂两次结论一定一样.

    (注意它**不能**凭报告 JSON 事后重判: 报告里存的是跑次的汇总与 `run_id`, 不是
    完整事实 —— 想重判就顺着 `run_id` 回记录层取那一跑的轨迹, 见 `RunFacts`.)

    **可以抛错**: 抛了由跑批器记成一条 `judge_errors` 继续跑下一题 —— 一个判据在
    某道题上崩了, 不该把整批 60 次运行的结论一起带走.
    """

    def judge(self, case: EvalCase, facts: RunFacts) -> Judgment:
        """判这一跑.

        Args:
            case: 这道题 (期望工具集 / 期望参数都在它身上).
            facts: 这一跑的事实.

        Returns:
            Judgment: 这一跑在这个判据上的结论与可汇总的子指标.
        """
        ...


# 跑批器收的工厂: 给这道题, 造一个这一次跑用的被测对象.
#
# 为什么是异步的: 装配一个会话在两处必然要 await (建模型客户端 / 打开记录层),
# 而让业务侧在同步函数里偷偷 `asyncio.run` 会踩「连接池绑定别的循环」那个坑
# (client/app.py 的模块 docstring 记过这一条).
SubjectFactory = Callable[[EvalCase], Awaitable[EvalSubject]]
