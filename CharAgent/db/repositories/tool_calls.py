"""工具调用 (ToolCall) 的存取: 记下模型要调什么、结果如何、谁批的.

一句话理解: 这是**工具调用柜员**. 它记的不是「工具怎么执行」(那是 tool 包的事),
而是**执行前后留下了什么**: 模型填了什么参数、结果是什么、耗了多久、高危动作是
谁批的.

**主键为什么有三列** (`run_id` + `message_id` + `tool_call_id`): 上游模型每一轮
都从 `call_0` 重新编号 —— 同一个运行里会出现好几次「call_0」(第一轮一次、第二轮
又一次). 两列的键 (`run_id` + `tool_call_id`) 只解决「跨运行重复」, 同一个运行
的多轮之间照样撞. 真正唯一的身份是「**哪条 assistant 消息**发起的这一次调用」,
所以 `message_id` 也是键的一部分. 这一点在 `checkpoint/utils/pending.py` 里有
同样的说明 (那边是按 id 建字典会读错).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from CharAgent.db.entities import ToolCall, ToolCallStatus
from CharAgent.db.errors import DataConfigError, DataStoreError
from CharAgent.db.repositories.base import PgRepository
from CharAgent.db.schema import runs, tool_calls


def build_tool_call(
    *,
    run_id: str,
    message_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: str = "",
    status: ToolCallStatus = ToolCallStatus.PENDING,
    approval_prompt: str | None = None,
    approval_needs: Sequence[str] | None = None,
    created_at: datetime | None = None,
) -> ToolCall:
    """造一条工具调用记录 (不落库) —— 批量写之前先把行备好时用.

    与 `ToolCallsRepository.add` 共用同一套默认值: 一批调用与单条调用写出来的行
    形状必须一样, 否则查出来的数据会因为「怎么写的」而不一致.

    Args:
        run_id: 属于哪次运行.
        message_id: 是哪条 assistant 消息发起的 —— **必填**, 它是主键的一部分:
            上游每轮从 call_0 重新编号, 同一个 run 里会有好几条同名调用, 只有
            「哪条消息发起的」能把它们分开.
        tool_call_id: 模型给的调用编号.
        tool_name: 工具名.
        arguments: 模型填的原始 JSON 字符串 (不预解析).
        status: 初始状态.
        approval_prompt / approval_needs: 挂起等人时**要问用户什么** (#25):
            给用户看的那句话, 以及还缺什么 (机器可读的短名字). 只有 `needs_approval`
            那一条会带; None = 不是要人批的调用.
        created_at: 显式时刻 (测试用); None 则取当下 (UTC).
    """
    moment = created_at if created_at is not None else datetime.now(UTC)
    return ToolCall(
        run_id=run_id,
        message_id=message_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
        status=status.value,
        result=None,
        duration_ms=None,
        approved_by=None,
        approved_at=None,
        approval_prompt=approval_prompt,
        approval_needs=None if approval_needs is None else list(approval_needs),
        created_at=moment,
        updated_at=moment,
    )


class ToolCallsRepository(PgRepository):
    """工具调用表的读写口."""

    async def add(
        self,
        *,
        run_id: str,
        message_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: str = "",
        status: ToolCallStatus = ToolCallStatus.PENDING,
        approval_prompt: str | None = None,
        approval_needs: Sequence[str] | None = None,
        created_at: datetime | None = None,
    ) -> ToolCall:
        """记下一条工具调用 (刚发起时通常是 pending).

        Args:
            run_id: 属于哪次运行.
            message_id: 是哪条 assistant 消息发起的 (**必填**, 主键的一部分).
            tool_call_id: 模型给的调用编号.
            tool_name: 工具名.
            arguments: **模型填的原始 JSON 字符串** (不预解析 —— 畸形 JSON 正是
                自纠错路径的信号, 解析了反而丢证据).
            status: 初始状态.
            approval_prompt / approval_needs: 挂起等人时要问用户什么 (见
                `build_tool_call`); None = 不是要人批的调用.
            created_at: 显式时刻 (测试用); None 则取当下.

        Returns:
            ToolCall: 记好的实体.

        Raises:
            DataStoreError: 写库失败 (同一条消息里同样的 tool_call_id 已经有了).
        """
        row = build_tool_call(
            run_id=run_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
            approval_prompt=approval_prompt,
            approval_needs=approval_needs,
            created_at=created_at,
        )
        async with self._session() as session:
            try:
                session.execute(tool_calls.insert().values(**self._params(row)))
            except IntegrityError as exc:
                # 报「唯一约束被违反」对调用方没用 —— 它不知道是哪一条、为什么重.
                # 换成一句能照着排查的话 (与 threads.add / runs.add 同一套做法).
                raise DataStoreError(
                    f"这次工具调用没能记下来 (run_id={run_id!r}, "
                    f"message_id={message_id!r}, tool_call_id={tool_call_id!r} "
                    f"可能已经记过了): {exc.orig}"
                ) from exc
            return self._one(session, run_id, message_id, tool_call_id)

    async def add_calls(self, *, run_id: str, calls: Sequence[ToolCall]) -> None:
        """批量记下一轮里的多条调用 (并行语义: 同一轮的多条一起进来).

        与 `add` 的分工: `add` 面向「一条一条来」的调用方 (测试、单条补记),
        `add_calls` 面向「模型一口气要调三个工具」的批量场景 —— 也省掉了每条
        一次事务的开销. 行由 `build_tool_call` 造 (两个方法共用同一套默认值).

        **这个入口是幂等的** (ticket 27 起): 三列主键已经在了就跳过
        (`ON CONFLICT DO NOTHING`). 记录层要「执行前落 pending + 执行后回填终态」,
        而收尾那一趟是**补齐**(哪一次没写上就补上) —— 于是同一批调用会被交给它两次,
        第二次必须什么都不做, 且**不改已存在那一行的结论** (推进终态走 `set_status`).

        Raises:
            DataConfigError: 这批调用跨了多次运行 (主键里有 run_id, 混着写几乎
                总是调用方把两次运行的数据串了).
        """
        if not calls:
            return
        run_ids = {call.run_id for call in calls}
        if len(run_ids) > 1:
            raise DataConfigError(
                f"同一批工具调用只能属于一次运行, 实际跨了 {len(run_ids)} 次: "
                f"{sorted(run_ids)}"
            )
        # 同一轮的多条调用是**同一时刻**发起的, 若不拉开一点时间, 它们的
        # created_at 完全相同, `list_for_run` 的顺序就只能靠主键 —— 而主键里有
        # 模型给的编号, 顺序就成随机的了. 差 1 微秒即可保证稳定可复现.
        base = calls[0].created_at
        for offset, call in enumerate(calls):
            call.created_at = base + timedelta(microseconds=offset)
        async with self._session() as session:
            session.execute(
                # ON CONFLICT DO NOTHING 是 Postgres 的方言写法 (与
                # checkpoint/postgres.py 同一个 import): 本层的库就是它
                pg_insert(tool_calls).on_conflict_do_nothing(),
                [self._params(call) for call in calls],
            )

    async def get(
        self, run_id: str, message_id: str, tool_call_id: str
    ) -> ToolCall | None:
        """按 (运行, 消息, 调用编号) 取一条 (没有则 None)."""
        async with self._session() as session:
            return self._one(session, run_id, message_id, tool_call_id)

    async def list_for_run(self, run_id: str) -> list[ToolCall]:
        """列出一次运行里的全部工具调用 (按发起时间正序).

        「这次运行到底干了什么」看它 —— 轨迹断言 (#62) 与审计都从这里取.
        """
        statement = (
            select(ToolCall)
            .where(tool_calls.c.run_id == run_id)
            .order_by(
                tool_calls.c.created_at.asc(),
                tool_calls.c.message_id.asc(),
                tool_calls.c.tool_call_id.asc(),
            )
        )
        async with self._session() as session:
            return list(session.scalars(statement))

    async def set_status(
        self,
        run_id: str,
        message_id: str,
        tool_call_id: str,
        status: ToolCallStatus,
        *,
        result: object | None = None,
        duration_ms: int | None = None,
        approved_by: str | None = None,
        approval_prompt: str | None = None,
        approval_needs: Sequence[str] | None = None,
    ) -> bool:
        """更新一条调用的状态 (以及随之而来的结果 / 耗时 / 审批人 / 要问什么).

        Args:
            run_id / message_id / tool_call_id: 定位哪一条 (三列主键).
            status: 新状态.
            result: 执行结果或可操作错误信息; None 表示不动这一列 (不是「清空」).
            duration_ms: 耗时; None 表示不动.
            approved_by: 审批人 (#25); 给了它就会一并记下审批时刻.
            approval_prompt / approval_needs: 挂起等人时要问用户什么 (#25). 为什么要
                能**补写**: 那条行是「执行前那一拍」建的 (那时还不知道要不要人批),
                裁决出来之后才在这一拍补上 —— 建行走的是幂等插入 (已有的跳过),
                于是这两列只能靠这一笔写进去.
                **None = 不动这一列** (与 result 同一条约定): 绝大多数调用没有它们.

        Returns:
            bool: 改到了 True; False = 没有这一条.
        """
        now = datetime.now(UTC)
        values: dict[str, object] = {"status": status.value, "updated_at": now}
        if result is not None:
            values["result"] = result
        if duration_ms is not None:
            values["duration_ms"] = duration_ms
        if approved_by is not None:
            values["approved_by"] = approved_by
            values["approved_at"] = now
        if approval_prompt is not None:
            values["approval_prompt"] = approval_prompt
        if approval_needs is not None:
            values["approval_needs"] = list(approval_needs)

        statement = (
            update(tool_calls)
            .where(tool_calls.c.run_id == run_id)
            .where(tool_calls.c.message_id == message_id)
            .where(tool_calls.c.tool_call_id == tool_call_id)
            .values(**values)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    async def record_decision(
        self, run_id: str, message_id: str, tool_call_id: str, *, decided_by: str
    ) -> bool:
        """记下**谁在什么时候给了一次挂起的结论** (#25); 状态一个字都不碰.

        与 `set_status` 的分工: 那个推进的是「这条调用现在到哪一步了」, 这个是
        「人什么时候拍的板」. 两者**刻意分开**, 因为它们的时刻不同: 人点确认的那
        一刻, 那条调用还没执行 (它在恢复那一段里才跑), 而执行完又要写一次状态 ——
        合成一条语句的话, 后写的那次会把先写的时刻冲掉.

        ADR-0014 把「批了没有」定成一对列 (`approved_at IS NULL` = 还没批), 而
        「还有没有未决挂起」的判据是 `status = needs_approval AND approved_at IS
        NULL` —— 于是这一笔同时是**闸门**: 写下去之后, 那次挂起就不再拦新提问了.

        Args:
            run_id / message_id / tool_call_id: 定位哪一条 (三列主键).
            decided_by: 给结论的人 (业务侧的属主标识).

        Returns:
            bool: 改到了 True; False = 没有这一条.
        """
        statement = (
            update(tool_calls)
            .where(tool_calls.c.run_id == run_id)
            .where(tool_calls.c.message_id == message_id)
            .where(tool_calls.c.tool_call_id == tool_call_id)
            .values(
                approved_by=decided_by,
                approved_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    async def list_pending_approvals(self, thread_id: str) -> list[ToolCall]:
        """列出一段会话里**还挂着等人批**的调用 (早的在前).

        判据是 ADR-0014 定下的那一条 (`status = needs_approval` 且
        `approved_at IS NULL`), 「属于本会话」靠 join 运行行拿到 —— 这张表只有
        `run_id`, 而会话是运行行的属性.

        两个调用方都用它, 但问的是不同的问题:
        - 服务端收新提问时问「这段会话还挂着吗」(`SessionRegistry` 的闸门)
        - `GET /history` 问「挂着的是哪一条」(前端刷新之后重建确认卡)

        **一行 = 一次挂起**: 框架一次只挂一条 (见 `agent/loop.py`), 所以正常情况下
        最多一条; 返回列表是因为**判据**允许多条 (历史数据 / 别的调用方写进来的),
        而「只认第一条」这种裁量不该藏在这一层.

        Args:
            thread_id: 哪段会话.

        Returns:
            list[ToolCall]: 未决的挂起调用 (按发起时刻正序).
        """
        statement = (
            select(ToolCall)
            .join(runs, runs.c.run_id == tool_calls.c.run_id)
            .where(runs.c.thread_id == thread_id)
            .where(tool_calls.c.status == ToolCallStatus.NEEDS_APPROVAL.value)
            .where(tool_calls.c.approved_at.is_(None))
            .order_by(tool_calls.c.created_at.asc(), tool_calls.c.message_id.asc())
        )
        async with self._session() as session:
            return list(session.scalars(statement))

    @staticmethod
    def _params(call: ToolCall) -> dict[str, object]:
        """实体 → 插入参数."""
        return {
            "run_id": call.run_id,
            "message_id": call.message_id,
            "tool_call_id": call.tool_call_id,
            "tool_name": call.tool_name,
            "arguments": call.arguments,
            "status": call.status,
            "result": call.result,
            "duration_ms": call.duration_ms,
            "approved_by": call.approved_by,
            "approved_at": call.approved_at,
            "approval_prompt": call.approval_prompt,
            "approval_needs": call.approval_needs,
            "created_at": call.created_at,
            "updated_at": call.updated_at,
        }

    @staticmethod
    def _one(
        session: Session, run_id: str, message_id: str, tool_call_id: str
    ) -> ToolCall | None:
        """按三列主键取一行 (强制读库里的最新值, 不吃会话缓存).

        `expire_all()` 的作用见 `RunsRepository._one` —— 刚改完状态再查却拿到
        内存里那份旧对象, 这种 bug 极难看出来.
        """
        session.expire_all()
        return session.get(ToolCall, (run_id, message_id, tool_call_id))
