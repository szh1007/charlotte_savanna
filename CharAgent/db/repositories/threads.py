"""会话 (Thread) 的存取: 建会话、按编号取、列一个租户/用户的会话列表、改会话.

一句话理解: 这是**会话柜员**. 上层要一段对话的壳子, 找它; 要「某个用户最近聊了
哪些」, 也找它; 要改标题 / 置顶 / 删除, 也找它.

多租户的落点 (difficulties #32): **凡按条件找人 / 查列表的方法**都强制带 tenant_id
条件, 签名里也**不给**「不加租户」的选项 —— 多租户项目里最容易出的事故就是某处忘了
加这个条件, 把 A 公司的会话返回给了 B 公司. 少一个可选参数, 就少一条能出这种事的路.

(按主键单个取的办法 (`get` / `touch` / `set_title`) 不带: 主键本身在整张表里就是唯一
的, 带上租户只是多一个条件, 不改变「取到的是哪一行」. 三个写方法则**都带** —— 它们
比读危险, 见下面 `_owned` 那条.)

三个写方法 (改标题 / 置顶 / 删除, #20) 把那条件收到**一个 keyword-only 的必填组**
里 (`tenant_id` / `user_id`, 见 `_owned`): 它们比读更危险 —— 读错了只是看见别人的,
写错了是**改**了别人的. 「归属」这一条因此不进签名默认值, 漏了就报 TypeError.

| 方法 | 取的是什么 | 谁用 |
|------|-----------|------|
| `list_for_tenant` | 这个租户(这个用户)的**全部**会话 | 管理端 / 排查 / 对账 |
| `list_active_with_messages` | 其中**聊过话且还活着**的那些 | 前端左侧的会话列表 |

第二个为什么不在调用方过滤 (拿到列表再逐条查消息 = N+1 次往返, 而这件事数据库
一次就做完了): 「有可见消息」是个 EXISTS 条件, 它同时也是**列表该有什么**的一部分
—— 没聊过的空壳会话 (前端点「新建」那一刻建的) 不该出现在列表里.

**「活着」与「没被删」是两件事, 但都归列表管** (#20): `status` 说的是这段对话在
业务上还算不算进行中 (`active` / `closed` / `escalated`), `deleted_at` 说的是用户
有没有把它从自己的列表里删掉. 两个条件都由 `_visible()` 一处给出 —— 散在各处的话,
「删掉的会话还出现在列表里」这种漏只会在某一条查询上出现.

**搜索是列表的一个过滤条件, 不是另一个方法**: `list_active_with_messages` 多收一个
`query=None` —— 端点数量不涨 (`GET /conversations?q=`), 而「所有会话查询都带
`deleted_at IS NULL`」这条也才有唯一一处可守. 真正的搜索服务 (全文索引 / 相关性
排序) 是另一个量级的事, 本项目不做.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import ColumnElement, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from CharAgent.db.entities import Thread, ThreadStatus
from CharAgent.db.errors import DataStoreError
from CharAgent.db.repositories.base import PgRepository
from CharAgent.db.schema import messages, threads

# 列表查询的默认上限: 不设上限的「列全部」在数据长起来之后会拖垮接口,
# 要用更多就显式传 limit (让「取多少」是一个被想过的决定).
DEFAULT_LIST_LIMIT = 50


def _visible() -> ColumnElement[bool]:
    """「用户还看得见这个会话」—— 没被删掉.

    单独一个函数而不是把条件写进每条查询: 会话查询有两条 (列全部 / 列还有效的),
    以后还会有别的; 条件写在两处就有一处会漏, 而漏的表现是「删掉的会话又出现在
    列表里」—— 用户刚删完刷新一下又回来了, 看着像删除根本没生效.
    """
    return threads.c.deleted_at.is_(None)


def _owned(tenant_id: str, user_id: str) -> ColumnElement[bool]:
    """「这个会话是这次请求的那个人的」—— 写方法唯一一道门槛.

    写成组合条件而不是两个 `.where()`: 它表达的是**一件事** (归属), 调用处
    (三个写方法各一行) 因此看不出「可以只带一半」的余地.

    注意它**不含** `_visible()`: 删除要对一段已经删掉的会话幂等 (再删一次该是
    「还是删着的」而不是「找不到」), 而另外两个动作作用在已删的会话上也无害 ——
    它已经不在任何人的列表里, 改不改都不影响谁.
    """
    return (threads.c.tenant_id == tenant_id) & (threads.c.user_id == user_id)


def _visible_messages() -> ColumnElement[bool]:
    """「这条消息属于这个会话, 而且是给用户看的」—— EXISTS 的关联条件.

    关联与过滤写在一起, 于是「可见」只有一个定义: `thread_id` 对得上, 且
    `hidden = false`.

    调用方**只剩一个** (列表的「聊过话吗」) —— 搜索收窄到只搜标题之后
    (ticket 25), 它不再被搜索用. 仍然不并进调用方: 「这条消息给不给人看」是记录
    那一层的事 (CONTEXT.md 的「记录」: 「每行标着要不要展示给前端」), 不是一个
    随手拼的条件.
    """
    return (messages.c.thread_id == threads.c.thread_id) & messages.c.hidden.is_(False)


def _has_visible_message() -> ColumnElement[bool]:
    """「这段对话里有一条用户看得见的消息」.

    空壳会话 (点了「新建」还没开口) 与只剩内部件的会话都不该进列表 —— 它们点进去
    也是一片空白. 判据是**有没有**而不是**有几条**, 所以用 EXISTS 不用 COUNT:
    数据库找到第一条就能停.
    """
    return exists().where(_visible_messages())


def _escape_like(text: str) -> str:
    """把用户输入里的 LIKE 通配符转义掉 (`\\` 自己也要先转).

    不转的话, 搜一个 `_` 会命中**所有**会话 (`_` 是「任意一个字符」), 看着像搜索
    坏了; 而 `_` 恰恰是很容易被敲进去的一个键. 转义符本身也走一遍, 免得用户输入
    的 `\\` 把后面的字符意外转义掉.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _matches(query: str) -> ColumnElement[bool]:
    """「标题里有这个词」—— 搜索的判据 (#20, 收窄于 ticket 25).

    为什么**只搜标题** (而 #20 那一版连正文一起搜): 列表按 `updated_at` 排, 本
    项目没有相关性排序 —— 留着正文搜索, 就会长期给出「排序说不通」的结果 (正文里
    顺带提了一句的会话, 排在标题明显更相关的那个前面). 那比搜不到更伤信任.

    代价明写: 「记得聊过 XX 却想不起标题」这条路**搜不到了**. 要补它得做「消息
    搜索」—— 那是另一个概念 (搜会话 vs 搜消息), 见 `CharApp/CONTEXT.md`.
    """
    pattern = f"%{_escape_like(query)}%"
    return threads.c.title.ilike(pattern, escape="\\")


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

        **被用户删掉的会话不在这里** (#20): 「列全部」是给管理端 / 排查 / 对账用的,
        而「删掉的还算不算这个租户的会话」这个问题只有一个诚实的答案 —— 数据还在,
        但它已经不在任何人的视野里. 要连删掉的一起看, 得另开一个显式说明意图的方法
        (比如 `include_deleted=True`); 本项目暂时没有这个需求, 所以**不铺**.

        排序也**不带置顶**: 置顶是「我的列表」里的一个偏好, 而这条查询问的是
        「这个租户有什么」—— 把某个用户的个人偏好混进对账口径里只会让两边都难解释.

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
            .where(_visible())
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
        query: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[Thread]:
        """列**还能聊、聊过话、没被删**的会话 (置顶的在前) —— 前端会话列表要的.

        「聊过话」的判据是**有一条可见消息** (`hidden = false`): 空壳会话 (点了
        「新建」还没开口) 与只剩内部件的会话都不进列表 —— 它们点进去也是一片空白.

        Args:
            tenant_id: 租户 (必填, 理由见模块 docstring).
            user_id: 只看这个属主的会话; None 表示这个租户下所有用户的.
            query: 搜索词; None / 空串 = 不搜. 给了就只回**标题**命中它的会话
                (大小写不敏感) —— 判据与理由见 `_matches` 那段.
            limit: 最多几条 (<= 0 返回空列表).

        Returns:
            list[Thread]: 置顶的在最前 (最近置顶的靠前), 其余按 updated_at 倒序.
        """
        if limit <= 0:
            return []
        # 状态 / 「有可见消息」/「没被删」都是**过滤条件**而不是取回来再算的: 让
        # 数据库用一条查询给出最终列表, 调用方不必为每一行再问一次消息表 (N+1)
        statement = (
            select(Thread)
            .where(threads.c.tenant_id == tenant_id)
            .where(threads.c.status == ThreadStatus.ACTIVE.value)
            .where(_visible())
            .where(_has_visible_message())
            .order_by(
                # 置顶的永远在最前, 而**最近置顶的**排在更前面 (这就是取时刻而不是
                # 布尔的意义); `NULLS LAST` 让没置顶的那些跟在后面 —— 少了它 PG 的
                # 默认是 DESC 时 NULL 在最前, 列表会被没置顶的占满而置顶的沉底.
                threads.c.pinned_at.desc().nulls_last(),
                threads.c.updated_at.desc(),
                threads.c.thread_id.desc(),
            )
            .limit(limit)
        )
        if user_id is not None:
            statement = statement.where(threads.c.user_id == user_id)
        if query and query.strip():
            statement = statement.where(_matches(query.strip()))
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

    async def set_title(self, thread_id: str, title: str) -> bool:
        """给会话补上标题 (首条用户消息来的那一刻).

        为什么要单独一个方法 (ticket 22): 运行行有外键指着会话行, 于是记录员在
        **跑之前**就得把会话行建出来, 而那一拍未必带着标题 (protocol 里 `title`
        的默认值就是空串) —— 先建一个空标题的, 收尾那拍用首条用户消息补上.
        `touch` 那条写法的同款 (一条带 WHERE 的 UPDATE, 如实回报改没改到).

        **只补空标题**: 标题的语义是「首条用户消息」(见 `title_for`), 已有标题的
        会话再给也不覆盖 —— 否掉「聊到一半标题被换成最新那句」这种漂移.

        Args:
            thread_id: 哪个会话.
            title: 规范化后的标题 (调用方 `normalize_title` 过).

        Returns:
            bool: 改到了行 True; False = 没有这个会话, 或它已经有标题了.
        """
        statement = (
            update(threads)
            .where(threads.c.thread_id == thread_id, threads.c.title == "")
            .values(title=title, updated_at=datetime.now(UTC))
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    async def update_title(
        self, thread_id: str, title: str, *, tenant_id: str, user_id: str
    ) -> bool:
        """用户给会话改名 (#20) —— **只改标题, 不动 `updated_at`**.

        为什么不能顺手刷 `updated_at`: 那个列是「最后**活动**时刻」, 列表按它倒序
        —— 改个标题就把会话顶到列表最前, 等于把「我刚改了个名字」说成「我刚聊过」.
        真正刷它的是 `touch` (写完一轮记录时调).

        与 `set_title` 的分工: 那个是**系统**补的自动标题 (只在标题为空时写一次,
        收了第一条用户消息就再不动), 这个是**用户**改的 (无条件覆盖). 两条路各有
        一个方法、各有自己的判据 —— 合成一个的话, 「用户的命名会不会被覆盖」这个
        问题就得看调用方心情.

        Args:
            thread_id: 哪个会话.
            title: 新标题 (非空与长度那条线画在调用方 —— 见
                `server.conversations.MAX_TITLE_LENGTH`: 那是**接口**的规矩,
                而这一层只负责把给它的值写进去).
            tenant_id / user_id: 归属 (必填 keyword, 见 `_owned`).

        Returns:
            bool: 改到了行 True; False = 没有这个会话, 或者它不是这个人的.
        """
        statement = (
            update(threads)
            .where(threads.c.thread_id == thread_id, _owned(tenant_id, user_id))
            .values(title=title)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    async def set_pinned(
        self,
        thread_id: str,
        pinned: bool,
        *,
        tenant_id: str,
        user_id: str,
        moment: datetime | None = None,
    ) -> bool:
        """置顶 / 取消置顶一个会话 (#20): 置顶写当下, 取消置顶写回 NULL.

        为什么取消是写 NULL 而不是另开一列: 「有没有置顶」的语义在这一列上就是
        `IS NULL` —— 列表的排序 (`pinned_at DESC NULLS LAST`) 与前端「现在该显示
        置顶还是取消置顶」读的是同一个东西, 两边一个口径.

        Args:
            thread_id: 哪个会话.
            pinned: True 置顶 (写当下) / False 取消 (写 NULL).
            tenant_id / user_id: 归属 (必填 keyword, 见 `_owned`).
            moment: 显式时刻 (测试用); None 则取当下 (UTC) —— 只影响置顶那一支.

        Returns:
            bool: 改到了行 True; False = 没有这个会话, 或者它不是这个人的.
        """
        stamp = moment if moment is not None else datetime.now(UTC)
        statement = (
            update(threads)
            .where(threads.c.thread_id == thread_id, _owned(tenant_id, user_id))
            .values(pinned_at=stamp if pinned else None)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    async def soft_delete(
        self,
        thread_id: str,
        *,
        tenant_id: str,
        user_id: str,
        moment: datetime | None = None,
    ) -> bool:
        """把会话从用户的列表里删掉 (#20) —— **软删**: 行与它名下的消息都留着.

        为什么不做真删: 硬删会顺着外键把运行 / 消息 / 工具调用 / 帧一起 CASCADE
        掉, 而成本记账 (L3) 与「模型为什么忘了」都挂在那几张表上. 用户要的「删除」
        本来就是「从我的列表里消失」, 这一列完全满足.

        **重删幂等**: WHERE 里只有归属 (`_owned`), **不含**「还没删过」—— 所以第二次
        调用照样命中一行、照样回 True, 上层回的就是 200 而不是 404. 代价是那一次的
        时刻会被覆盖 (想留住第一次要写 `coalesce(deleted_at, :now)`) —— 不换: 那是
        一个表达式, 而本仓储的写入形状一律是「等值条件 + 直接给值」(测试替身也只认
        这个形状), 为了一列排查用的时间戳破掉它不值.

        Args:
            thread_id: 哪个会话.
            tenant_id / user_id: 归属 (必填 keyword, 见 `_owned`).
            moment: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            bool: 命中了行 True; False = 没有这个会话, 或者它不是这个人的.
        """
        stamp = moment if moment is not None else datetime.now(UTC)
        statement = (
            update(threads)
            .where(threads.c.thread_id == thread_id, _owned(tenant_id, user_id))
            .values(deleted_at=stamp)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    @staticmethod
    def _params(thread: Thread) -> dict[str, object]:
        """实体 → 插入参数 (放在这里而不是通用工具里: 只有本仓储写这张表).

        两个管理列 (`pinned_at` / `deleted_at`) 也在这里, 尽管新建时它们**必然**
        是 NULL (没有「一建出来就置顶 / 已删」这回事): 这张字典是「本表有哪些列」
        的一份镜像, 少两行的话, 以后谁给 `add` 加一个 `pinned=...` 参数时就会对着
        它找不到落点.
        """
        return {
            "thread_id": thread.thread_id,
            "tenant_id": thread.tenant_id,
            "user_id": thread.user_id,
            "title": thread.title,
            "status": thread.status,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
            "pinned_at": thread.pinned_at,
            "deleted_at": thread.deleted_at,
        }

    @staticmethod
    def _one(session: Session, thread_id: str) -> Thread | None:
        """按主键取一行 (返回实体而不是裸行, 上层拿到的就是有方法的对象)."""
        return session.get(Thread, thread_id)
