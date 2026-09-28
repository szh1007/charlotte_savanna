"""跑分环境 (issue 41): 一次问答要用的那套零件.

一句话理解: 把**假商城 + 内存快照 + 假记录库 + 生产那套服务装配**打包成一份,
跑分器 (issue 42 的题与判据 / 43 的挂起-恢复 / 44 与 45 的两个 A/B) 拿它跑问答,
再按运行编号把那一跑的轨迹读回来.

为什么是这一套 (而不是真商城 / 真 Postgres): 跑分要跑几十上百次问答, 而它要量的
是**模型**那部分 —— 商城走 respx 假端点、记录落在内存里的假库、快照在内存里, 于是
跑分既不依赖 Django 也不依赖真库. 打真模型的那一半照旧 (L4 规划期定的: 真模型 +
假商城 + 只规则判).

**「不出网」只管商城那一半**: 模型是真的, 它的请求当然要出网 —— 而它走的也是 httpx,
正是假商城拦的那条通道. 于是 `open_harness` 在铺完商城那几条路由之后挂一条**兜底
放行** (见 `fixtures.pass_through_the_rest`). 少了它, 每一跑都会以「POST
api.deepseek.com ... not mocked!」记成 `BROKEN` (issue 44 冒烟时真撞上过).

**三个已知的坑, 用之前先知道**:

1. **假库不过滤 `WHERE` 也不排序** (`CharAgent/db/testing.py` 的模块 docstring):
   在它上面 `ToolCallsRepository.list_for_run(run_id)` 会把**全库**的工具调用行交
   回来 —— 读某一次运行的轨迹要自己按 `call.run_id` 筛, 也就是 `calls_of`.
2. **`FakeRecordDatabase` 原先没有 `dispose`**, 而 `MinimallService.aclose()` 会调
   它, 且是在关掉模型 / 快照 / 客户端**之后**才调 —— 少了那个空实现, 收尾那一刻
   会抛一个与真因无关的 `AttributeError` (issue 41 补上了).
3. **假库不校验外键** —— 于是 ticket 24 记的那条死路 (`saver` 是 Postgres 而
   `database=None` 时第一句问话会外键失败) 在本片这只组合上**不成立**:
   `InMemoryCheckpointSaver()` 写帧时不要求 `charagent_threads` 里有那一行.

**模型的所有权随装配一起交进来**: `open_harness` 收尾时经 `service.aclose()` 把它
一起关掉, 所以跑分器要**为每一跑造一份** (共享一个模型会让第一跑结束时把它关掉).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import respx

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client import ChatSession
from CharAgent.db import ToolCallsRepository
from CharAgent.db.entities import ToolCall, ToolCallStatus
from CharAgent.db.testing import FakeRecordDatabase
from CharAgent.model.protocol import ChatModel
from CharApp.eval.fixtures import (
    AGENT_BASE_URL,
    BUYER_ID,
    TOKEN,
    build_mall,
    mock_all,
    pass_through_the_rest,
)
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import context_config_from_env, thinking_from_env
from CharApp.minimall.scoping import ToolScope
from CharApp.minimall.service import TENANT_WEB, MinimallService, build_context


@dataclass
class EvalHarness:
    """跑分环境: 装好的一份服务 + 它的假商城与假记录库.

    attributes:
        service: 生产那套装配 (两入口共用的 `MinimallService`), 零件全换成了替身.
        records: 记录层的假库 (跑完一轮看它 —— 每行的 `run_id` 是这一跑的编号).
        routes: 假商城那 18 条路由 (`"GET cart/"` 这样的键), 按它断言「真打到商城
            了没有」—— 拒绝钩子那类用例靠它证「压根没打出去」(issue 44).
    """

    service: MinimallService
    records: FakeRecordDatabase
    routes: dict[str, respx.Route]

    def context(self, conversation: str, *, user_id: int = BUYER_ID) -> RunContext:
        """一段会话的运行上下文 (身份与租户照生产那条线组).

        租户用 `TENANT_WEB`: 跑分模拟的就是买家在网页里提问, 与命令行那个
        (`TENANT_CLI`) 是两回事.

        Args:
            conversation: 会话编号的第三段 —— 跑分时**每次问答给一个新值**
                (如 `"case-03-attempt-2"`), 于是题与题之间不共享上文, 而编号本身
                在记录里看得出来这是哪一题的第几次.
            user_id: 这次以哪个买家的身份提问. 默认就是样本里那个; 换一个是为了
                **换一只购物车** —— 假商城的车按 `X-User-Id` 分 (`BIG_CART` 那只
                是跑分专用的, 用来让护栏那条 5000 上限真的被撞到), 题集里由
                `meta["buyer_id"]` 指名 (见 `golden.py`).

        Returns:
            RunContext: 买家身份 + 租户 + 那段会话编号组成的上下文 (载荷里带着
            买家 ID, 与两个生产入口拿到的是同一个形状).
        """
        return build_context(user_id, conversation, tenant_id=TENANT_WEB)

    async def session(
        self, context: RunContext, *, scope: ToolScope | None = None
    ) -> ChatSession:
        """按生产那条线装一台会话 (走 `MinimallService.session_for`).

        事件出口是个丢弃器 (`redact=False`): 跑分不展示事件, 报告要的都在
        `RunFacts` 与轨迹里 —— 于是这里没有「浏览器」那一跳可脱敏, 与命令行
        (`redact=False`) 同一个口径.

        Args:
            context: 这次运行的上下文 (见 `context`).
            scope: 这一次运行给模型开哪几组工具 (issue 44 的裁剪组); None = 不裁
                (对照组). 挂起恢复那一段要用**同一个** scope —— 两段是同一次运行的
                两截, 中途换一套可见集会让轨迹说不清 (见 `subject.py`).

        Returns:
            ChatSession: 装着工具 + 护栏 + 记录员的会话 (可以连续问很多句).
        """
        return await self.service.session_for(
            context, event_sink=lambda event: None, redact=False, scope=scope
        )

    def calls_of(self, run_id: str | None) -> list[ToolCall]:
        """这一次运行调过的工具 (按发生序) —— **自己按 `run_id` 筛**.

        为什么不用仓储的 `list_for_run`: 假库的 `scalars` 不过滤 `WHERE` 也不排序
        (见模块 docstring 的坑 1), 在它上面那个方法会把全库的调用行交回来. 按
        `run_id` 筛是这条路上唯一可靠的读法, 而它在真假两个库上给出同一个结果.

        排序照 `list_for_run` 的口径 (发起时间 → 消息 → 调用编号): 同一轮里的多条
        调用是同一时刻发起的, 光看时间分不开.

        Args:
            run_id: 运行编号 (`ChatSession.last_run_id`); None = 这次没挂记录员
                或还没跑过 —— 交空列表 (与命令行那句「没有记录层就不附运行编号」
                同一条口径).

        Returns:
            list[ToolCall]: 这一跑调过的行, 按发生序; 一次都没调 -> 空列表.
        """
        if run_id is None:
            return []
        return sorted(
            (call for call in self.records.tool_calls if call.run_id == run_id),
            key=lambda call: (call.created_at, call.message_id, call.tool_call_id),
        )

    def cost_of(self, run_id: str | None) -> float | None:
        """这一跑折算的金额 (元); None = 没算 (记录层没写 / 这一跑没走记录层).

        从运行行读而不是自己乘一遍价目表: 那一行是**收尾那一刻**按当时的价目表算好
        的 (`db/cost.py`, 峰谷价还按那次运行的开始时刻判), 跑分再算一遍迟早与它分家
        —— 而分家的那天没有任何地方会报错.

        与 `calls_of` 同一个位置的理由也一样: 「这一次运行的记录」是记录层的事, 假库
        那点脾气 (不过滤 `WHERE`) 归这一层解释, 跑分器只管拿数.

        Args:
            run_id: 运行编号 (`ChatSession.last_run_id`); None = 这一跑没走记录层 ——
                交 None (与「没配价目表」同一个落点, 都不是 0).

        Returns:
            float | None: 金额; 找不到那一行或那一行没算出来都是 None.
        """
        if run_id is None:
            return None
        row = next((run for run in self.records.runs if run.run_id == run_id), None)
        return None if row is None or row.total_cost is None else float(row.total_cost)

    async def pending_approvals(self, thread_id: str) -> list[ToolCall]:
        """这一段会话里**还挂着等人批**的那几条调用 (早的在前).

        读的是记录层那条判据 (ADR-0014: 挂起态的家是那几行调用), 走的也是服务端
        同一个方法 (`ToolCallsRepository.list_pending_approvals`) —— 跑分器不自己
        另立一套「什么算挂起」. 跑分那一侧要它来: 判这一跑停没停在确认点上, 以及
        看那一条**点名要什么** (代付要点名密码, 见 `subject.py` 的模拟确认那一段).

        **假库上的三处补偿**: 那个方法靠三处筛选给出答案 (属于本会话 / 状态是
        `needs_approval` / 还没批过), 而假库既不过滤 `WHERE` 也不做 join (见模块
        docstring 的坑 1) —— 于是这三条在这里补上, 得到的集合与真库上**逐个相同**.
        会话那一层靠 `run_id` 回查运行行: `charagent_tool_calls` 只有 `run_id`,
        会话是运行行的属性, 真库那份 join 走的正是同一条路.

        Args:
            thread_id: 哪段会话 (跑分里 `harness.context(...)` 给的那个).

        Returns:
            list[ToolCall]: 未决的挂起调用 (按发起时刻正序); 没有就是空列表.
        """
        rows = await ToolCallsRepository(self.records).list_pending_approvals(thread_id)
        ours = {run.run_id for run in self.records.runs if run.thread_id == thread_id}
        return [
            row
            for row in rows
            if row.run_id in ours
            and row.status == ToolCallStatus.NEEDS_APPROVAL.value
            and row.approved_at is None
        ]


@asynccontextmanager
async def open_harness(model: ChatModel) -> AsyncIterator[EvalHarness]:
    """装一套跑分环境; 退出时把它建的全关掉 (模型也在内).

    零件与两个生产入口 (server / CLI) 的装配同源, 差别只在三样替换与一处不读:

    | 生产 | 这里 |
    |------|------|
    | `client_from_env()` | 接到 respx 假商城上的客户端 (地址与令牌是样本常量) |
    | `build_saver_for(...)` | `InMemoryCheckpointSaver` (跑分的会话不跨进程) |
    | `PgDatabase()` | `FakeRecordDatabase` (内存里的那几张表) |
    | `build_model_for(...)` | **交进来的模型** (真模型或 MockLLM) |

    **两处照旧从环境变量读** (`CHARAPP_THINKING` / `CHARAPP_CONTEXT_*`): 跑分要跑
    的正是生产那套参数 —— 报告头部那块配置快照 (issue 40 §四第 1 块) 记的就是它们,
    自己另定一套的话, 那句「这是哪套参数下的分」就落不到实处.

    Args:
        model: 这次跑分用的模型 —— **所有权随装配一起交出去**, 退出时经
            `service.aclose()` 关掉 (为每一跑造一份, 别共享).

    Yields:
        EvalHarness: 装好的零件 (服务 / 假记录库 / 假商城路由表).
    """
    with build_mall() as router:
        # 兜底放行:**模型那条路要出网** (跑分打的是真模型, 而它的请求也走 httpx ——
        # 那正是假商城拦的通道). 铺完商城那些路由之后再挂它, 于是模型照常连上游,
        # 而商城那几条照旧是假端点. 冒烟时真撞上过: 少了它, 每一跑都是 `BROKEN`,
        # 报的是「POST https://api.deepseek.com/chat/completions ... not mocked!」
        routes = mock_all(router)
        pass_through_the_rest(router)
        client = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
        records = FakeRecordDatabase()
        service = MinimallService(
            client=client,
            model=model,
            saver=InMemoryCheckpointSaver(),
            thinking=thinking_from_env(),
            database=records,
            compaction=context_config_from_env(),
        )
        try:
            yield EvalHarness(service=service, records=records, routes=routes)
        finally:
            await service.aclose()


__all__ = ["EvalHarness", "open_harness"]
