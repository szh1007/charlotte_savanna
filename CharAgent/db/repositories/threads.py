"""会话 (Thread) 的存取: 建会话、按编号取、列一个租户/用户的会话列表.

一句话理解: 这是**会话柜员**. 上层要一段对话的壳子, 找它; 要「某个用户最近聊了
哪些」, 也找它.

多租户的落点 (difficulties #32): 两个列方法都**强制**带 tenant_id 条件,
签名里也**不给**「不带租户查全部」的选项 —— 多租户项目里最容易出的事故就是
某处忘了加这个条件, 把 A 公司的会话列表返回给了 B 公司. 少一个可选参数, 就少
一条能出这种事的路.

两个列方法的取舍 (别随便挑一个用):

| 方法 | 取的是什么 | 谁用 |
|------|-----------|------|
| `list_for_tenant` | 这个租户(这个用户)的**全部**会话 | 管理端 / 排查 / 对账 |
| `list_active_with_messages` | 其中**聊过话且还活着**的那些 | 前端左侧的会话列表 |

第二个为什么不在调用方过滤 (拿到列表再逐条查消息 = N+1 次往返, 而这件事数据库
一次就做完了): 「有可见消息」是个 EXISTS 条件, 它同时也是**列表该有什么**的一部分
—— 没聊过的空壳会话 (前端点「新建」那一刻建的) 不该出现在列表里.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from CharAgent.db.entities import Thread, ThreadStatus
from CharAgent.db.errors import DataStoreError
from CharAgent.db.repositories.base import PgRepository
from CharAgent.db.schema import messages, threads

# 列表查询的默认上限: 不设上限的「列全部」在数据长起来之后会拖垮接口,
# 要用更多就显式传 limit (让「取多少」是一个被想过的决定).
DEFAULT_LIST_LIMIT = 50


class ThreadsRepository(PgRepository):
    """会话表的读写口 (方法只覆盖当前真正要用的场景, 不铺满 CRUD)."""

    async def add(
        self,
        *,
        tenant_id: str,
        user_id: str,
        title: str = "",
        thread_id: str | None = None,
        status: ThreadStatus = ThreadStatus.ACTIVE,
        created_at: datetime | None = None,
    ) -> Thread:
        """建一个会话 (编号与时刻默认自动生成).

        Args:
            tenant_id: 租户 (多租户隔离的过滤键).
            user_id: 会话属主.
            title: 标题; 空串表示「还没起名」(首条用户消息来了再补).
            thread_id: 显式指定编号 (测试要可复现的结果时用); None 则生成 uuid4 hex.
            status: 初始状态 (默认 active).
            created_at: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            Thread: 建好的实体 (带 thread_id / created_at / updated_at).

        Raises:
            DataStoreError: 写库失败 (编号撞了 / 库连不上).
        """
        moment = created_at if created_at is not None else datetime.now(UTC)
        thread = Thread(
            thread_id=thread_id if thread_id is not None else uuid4().hex,
            tenant_id=tenant_id,
            user_id=user_id,
            title=title,
            status=status.value,
            created_at=moment,
            updated_at=moment,
        )
        async with self._session() as session:
            try:
                session.execute(threads.insert().values(**self._params(thread)))
            except IntegrityError as exc:
                # 主键撞了 (同一个 thread_id 建两次) 时报「唯一约束被违反」对调用方
                # 没有帮助, 换成一句能照着排查的话
                raise DataStoreError(
                    f"会话没能建起来 (thread_id={thread.thread_id!r} 可能已存在): "
                    f"{exc.orig}"
                ) from exc
            # 插入后再读一次: 让**数据库补的默认值**也出现在返回对象里
            # (title / status / 时间列在库里有默认值, 由库填的才是真值)
            return self._one(session, thread.thread_id)

    async def get(self, thread_id: str) -> Thread | None:
        """按编号取一个会话 (没有则 None)."""
        async with self._session() as session:
            return self._one(session, thread_id)

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[Thread]:
        """列一个租户的会话 (最近活动的在前); 给了 user_id 就只看这个用户的.

        Args:
            tenant_id: 租户 (必填 —— 见模块 docstring).
            user_id: 只看这个属主的会话; None 表示这个租户下所有用户的.
            limit: 最多几条 (<= 0 返回空列表).

        Returns:
            list[Thread]: 按 updated_at 倒序 (刚聊过的在最前).
        """
        if limit <= 0:
            return []
        statement = (
            select(Thread)
            .where(threads.c.tenant_id == tenant_id)
            .order_by(threads.c.updated_at.desc(), threads.c.thread_id.desc())
            .limit(limit)
        )
        if user_id is not None:
            statement = statement.where(threads.c.user_id == user_id)
        async with self._session() as session:
            return list(session.scalars(statement))

    async def list_active_with_messages(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[Thread]:
        """列**还能聊且聊过话**的会话 (最近活动的在前) —— 前端会话列表要的那一份.

        「聊过话」的判据是**有一条可见消息** (`hidden = false`): 空壳会话 (点了
        「新建」还没开口) 与只剩内部件的会话都不进列表 —— 它们点进去也是一片空白.

        Args:
            tenant_id: 租户 (必填, 理由见模块 docstring).
            user_id: 只看这个属主的会话; None 表示这个租户下所有用户的.
            limit: 最多几条 (<= 0 返回空列表).

        Returns:
            list[Thread]: 按 updated_at 倒序 (刚聊过的在最前).
        """
        if limit <= 0:
            return []
        # 状态与「有可见消息」都是**过滤条件**而不是取回来再算的: 让数据库用一条
        # 查询给出最终列表, 调用方不必为每一行再问一次消息表 (N+1)
        statement = (
            select(Thread)
            .where(threads.c.tenant_id == tenant_id)
            .where(threads.c.status == ThreadStatus.ACTIVE.value)
            .where(
                exists().where(
                    (messages.c.thread_id == threads.c.thread_id)
                    & messages.c.hidden.is_(False)
                )
            )
            .order_by(threads.c.updated_at.desc(), threads.c.thread_id.desc())
            .limit(limit)
        )
        if user_id is not None:
            statement = statement.where(threads.c.user_id == user_id)
        async with self._session() as session:
            return list(session.scalars(statement))

    async def touch(self, thread_id: str, *, moment: datetime | None = None) -> bool:
        """刷新会话的「最后活动时刻」(写完一轮记录时调).

        为什么需要它: 会话列表按 `updated_at` 倒序排, 而那个值只在**建会话**那一
        刻写过一次 —— 不刷的话, 一段聊了三十轮的会话会永远停在创建时间上, 列表
        顺序就成了「谁先建的谁在下面」, 与「最近聊过的在最前」正好相反.

        `RunsRepository.set_status` 那条写法的同款 (一条带 WHERE 的 UPDATE,
        顺手把「改了没改到」如实回报给调用方); 单独开一个方法而不是让调用方自己
        拼 SQL: 要更新的只有这一列, 而它的语义 (活动时刻) 只有这里说得清.

        Args:
            thread_id: 哪个会话.
            moment: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            bool: 改到了行 True; False = 没有这个会话 (调用方多半是漏建了).
        """
        values = {"updated_at": moment if moment is not None else datetime.now(UTC)}
        statement = (
            update(threads).where(threads.c.thread_id == thread_id).values(**values)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    @staticmethod
    def _params(thread: Thread) -> dict[str, object]:
        """实体 → 插入参数 (放在这里而不是通用工具里: 只有本仓储写这张表)."""
        return {
            "thread_id": thread.thread_id,
            "tenant_id": thread.tenant_id,
            "user_id": thread.user_id,
            "title": thread.title,
            "status": thread.status,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
        }

    @staticmethod
    def _one(session: Session, thread_id: str) -> Thread | None:
        """按主键取一行 (返回实体而不是裸行, 上层拿到的就是有方法的对象)."""
        return session.get(Thread, thread_id)
