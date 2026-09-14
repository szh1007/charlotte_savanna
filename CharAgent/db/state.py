"""运行状态机的规则 (difficulties #16, 关联 #18 取消 / #25 HITL).

一句话理解: 一次执行 (Run) 的一生是**一条有固定走法的流程**, 不是一个可以随便
赋值的字段. 本文件就是那张「谁能走到谁」的规定表 —— 规定了:

- 从哪个状态出发, 允许去哪些状态 (`ALLOWED_TRANSITIONS`)
- 哪些状态是「到此为止」(`TERMINAL_RUN_STATUSES`)
- agent loop 的结束原因 (`LoopOutcome`) 该怎么翻成状态 (`RUN_STATUS_FOR_OUTCOME`)

**为什么状态后面要跟着一个「合法迁移表」而不是直接改字段**: 直接赋值挡不住
「把已经跑完的 run 改成运行中」这种写法 —— 代码能跑、测试不报, 但数据从此自相
矛盾 (最终答复都在了, 状态却显示还在跑). 有了这张表, 这类错误在**改的那一刻**
就抛 `InvalidTransitionError`, 并且报错里直接告诉你「本来能去哪儿」.

本模块**只有规则, 不碰数据库**: 上面那张表是纯数据, 拿去做离线测试与文档对照
都行. 真正落库的检查由 `RunsRepository.try_transition` 用一条带条件的 UPDATE
完成 (把规则下沉到 SQL 才能挡住并发, 见那个方法的 docstring).

状态机与 agent loop 的分工: **loop 决定「为什么停下」(LoopOutcome), 数据层
决定「停下之后这个 run 算什么状态」(本文件的映射表)**. 两边不互相 import 业务
逻辑, 靠一张映射表接上.
"""

from __future__ import annotations

from CharAgent.agent.utils.types import LoopOutcome
from CharAgent.db.entities import RunStatus
from CharAgent.db.errors import InvalidTransitionError

# --- 合法迁移表 ---------------------------------------------------------------

# 终态: 走到这里就结束了, 之后**没有任何出路**.
# 为什么终态不可逆: 「已经答完的 run 又变成运行中」意味着要重跑一遍真实动作
# (退款重做一次就是事故). 想重跑就**新建一个 run** —— 那是新的一次执行, 有自己
# 的编号与账目, 不该把老的那条记录改掉.
TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.FINISHED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

# 从每个状态出发, 允许去哪些状态.
# 读法: 左边是「现在」, 右边是「可以去」. 表里没写的一律非法.
#
# 几条值得说明的走法:
# - created → {running, failed, cancelled}: 刚建好还没跑就出问题 (进不了队列 /
#   被取消) 也要能收尾, 不能烂在 created 里.
# - running → {waiting_tool, waiting_user, retrying, finished, failed, cancelled}:
#   正在决策时, 下一步取决于模型这一轮说了什么 —— 要调工具 (waiting_tool)、
#   要高危操作等人批 (waiting_user)、上游挂了去重试 (retrying), 或者直接答完
#   (finished) / 出错 (failed) / 被叫停 (cancelled).
# - waiting_tool / waiting_user → {running, failed, cancelled}: 等的事情到了
#   (工具跑完 / 人批了) 就回到运行中接着走; 等的时候也可能失败或被取消.
# - retrying → {running, finished, failed, cancelled}: 重试成功后接着跑.
#   finished 这条出口是给一种真实情形留的: 重试发生在**模型调用层** (#13),
#   若上游在重试途中直接给出了完整答复, 这次 run 就算跑完了.
# - 终态在表里**一个出口都没有** (空集合), 于是任何离开终态的迁移都非法.
ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_TOOL,
            RunStatus.WAITING_USER,
            RunStatus.RETRYING,
            RunStatus.FINISHED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_TOOL: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.WAITING_USER: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.RETRYING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.FINISHED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.FINISHED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

# --- agent loop 的结束原因 → 运行状态 -----------------------------------------
#
# 映射的原则: **「跑完了但没给出答案」不算失败**. guard 刹车 (轮数 / token /
# 超时) 与截断放弃都是「按规则主动停下的」, 属于正常收尾 —— 它们的降级话术由
# 上层决定 (P1-8: 模板回复 / 转人工), 数据层只如实记一个 finished.
# 真正算 failed 的只有「上游把这次生成打断了」: 内容可能只有半截, 不能当答案.
#
# 另一个可能让人意外的点: 重试 (RETRYING) **不在这张表里**. 因为它是**过程状态**
# 而不是结束原因 —— 重试要么成功接着跑 (回到 running), 要么耗尽后以别的 outcome
# 收尾. LoopOutcome 描述的是「最后为什么停」, 那一刻不会是「正在重试」.
RUN_STATUS_FOR_OUTCOME: dict[LoopOutcome, RunStatus] = {
    LoopOutcome.FINISHED: RunStatus.FINISHED,
    LoopOutcome.MAX_TURNS: RunStatus.FINISHED,
    LoopOutcome.TOKEN_BUDGET: RunStatus.FINISHED,
    LoopOutcome.TIME_LIMIT: RunStatus.FINISHED,
    LoopOutcome.TRUNCATION_LIMIT: RunStatus.FINISHED,
    LoopOutcome.SERVER_INTERRUPTED: RunStatus.FAILED,
}


# --- 规则查询 -----------------------------------------------------------------


def can_transition(from_status: RunStatus, to_status: RunStatus) -> bool:
    """这次迁移合法吗 (只看允许表, 不碰数据库).

    给「先判断再决定」的调用方用 (比如 UI 上决定取消按钮要不要灰掉);
    真正要落库的迁移直接用 `RunsRepository.try_transition` —— 那边是原子的,
    不存在「判完再改」中间的并发窗口.

    Args:
        from_status: 当前状态.
        to_status: 想迁到的状态.

    Returns:
        bool: 合法 True; 非法 (或从终态出发) False.
    """
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


def ensure_transition(from_status: RunStatus, to_status: RunStatus) -> None:
    """不合法就抛错, 合法就放行 (在内存里推进状态时用).

    Args:
        from_status: 当前状态.
        to_status: 想迁到的状态.

    Raises:
        InvalidTransitionError: 这次迁移不在允许表里 (报错带上两边的状态与
            本来能去哪儿, 不用再去翻表).
    """
    if can_transition(from_status, to_status):
        return
    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    allowed_text = ", ".join(sorted(status.value for status in allowed)) or "无 (终态)"
    raise InvalidTransitionError(from_status.value, to_status.value, allowed_text)


def run_status_for_outcome(outcome: LoopOutcome) -> RunStatus:
    """把 agent loop 的结束原因翻成 run 的状态 (收尾时用).

    Args:
        outcome: `LoopResult.outcome`.

    Returns:
        RunStatus: 该记成什么状态.

    Raises:
        KeyError: outcome 没在映射表里 —— 说明 LoopOutcome 加了新成员而这里忘了
            补. 宁可当场报错 (开发期立刻发现), 也不要猜一个状态写进库.
    """
    return RUN_STATUS_FOR_OUTCOME[outcome]
