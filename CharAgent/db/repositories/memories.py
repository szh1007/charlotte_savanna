"""记忆 (Memory) 的存取: 记一句跨会话还有用的事实, 按时间衰减排序取回, 超量淘汰.

一句话理解: 这是**记忆柜员**. 上层 (C13 的 `remember` / `recall` 工具) 说
「记下这个用户偏好简短回复」, 找它; 说「这个用户还记得什么」, 也找它.

| 方法 | 干什么 | 谁用 |
|------|--------|------|
| `add` | 写一条 (重复只刷时间戳; **替换型顶掉旧值**) + 淘汰 | `remember` 工具 |
| `list_for_user` | 取回这个用户的活记忆, 按衰减分值排序 | `recall` 工具 |
| `list_by_id_prefix` | 按编号前缀找活行 (0 / 1 / 多条如实回) | `forget` 工具的第一步 |
| `touch_used` | 刷「最后被取回时刻」 (只记账, 不进排序) | `recall` 工具 |
| `soft_delete` | 作废一条记忆 (行留着) | `forget` 工具 / 人工清理 |

**两种 kind 行为** (C30): 累积型 (`episodic` / `semantic`) 多条并存、只增;
替换型 (`style` / `nickname`) 同一时刻只留一条, 新写顶掉旧的 (旧行软删, 何时被
换掉可查). 行为表在 `db/entities.py` 的 `KIND_BEHAVIORS` —— 枚举 + 单独规则表,
与运行状态机 (`state.py`) 同款.

**多租户的落点 (#32, MEM-D3)**: 每个方法 (读与写) 都强制带 `(tenant_id, user_id)`,
签名里**不给**「不加用户」的口子 —— 与 `threads.py` 同一条规矩: 少一个可选参数,
就少一条「把 A 的记忆递给 B」的路. 这是硬性安全要求, 不靠「相信模型不乱看」.

**排序分与淘汰用同一个口径** (difficulties #33): 分值 = `权重 * 衰减(now 与
created_at 的距离)`. 衰减取**指数衰减**: `0.5 ** (age_days / half_life_days)`
—— 新记忆 1.0, 每过一个半衰期减半; 半衰期与容量上限都是构造参数 (可配).

**权重这一半现在恒为 1**, 诚实地说: 模型不自评重要度 (C13 的取舍: 参数越少越
不容易调错), 也还没有访问计数 —— 「排序分 = 权重 * 衰减」的形状留着, 但这一票
没有引入权重的数据来源. 将来要引入 (比如「被 recall 取回过几次」), 在
`recency_score` 这一处相乘即可; `last_used_at` 那一列就是给这件事留的口.

**参数是拍的, 要如实说**: `DEFAULT_CAPACITY` (50) 与 `DEFAULT_HALF_LIFE_DAYS`
(30) 没有真实使用数据支撑 —— 是初始值, 等有真实使用数据再调. 写在这里是为了
复盘时能一眼看见「哪些数是拍的」, 而不是假装它们是调优出来的.

**容量淘汰**: 每个 (tenant, user) 的**活记忆**最多 `capacity` 条; `add` 之后
超出的部分按分值从低到高**软删** (先软删, 不物理删 —— 与 #31「废弃用软删标记
保留可追溯性」同源). 于是写入决策不需要「节制」这一个维度: 记多了靠淘汰收场
(C13 取舍里明说: 缓解手段是容量上限 + 衰减淘汰, 而不是靠 prompt 求它少写).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.orm import Session

from CharAgent.db.entities import KIND_BEHAVIORS, Memory, MemoryKind
from CharAgent.db.repositories.base import PgRepository
from CharAgent.db.schema import memories

# 每个 (tenant, user) 的活记忆条数上限. **拍的初始值** (见模块 docstring):
# 50 条一句话事实足够装下一个人的长期偏好, 超出的靠淘汰收场.
DEFAULT_CAPACITY = 50

# 时间衰减的半衰期 (天). **拍的初始值**: 30 天意味着「一条记忆放一个月, 分值
# 减半」—— 比一段对话活得久, 又不会让半年前的偏好压过上周的.
DEFAULT_HALF_LIFE_DAYS = 30.0

_SECONDS_PER_DAY = 86400.0


def recency_score(
    created_at: datetime,
    *,
    now: datetime,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """一条记忆在 `now` 这一刻的衰减分值 (排序与淘汰共用的那一个口径).

    指数衰减: `0.5 ** (age_days / half_life_days)` —— 刚写下是 1.0, 每过一个
    半衰期减半, 永远不归零. (A1 复利那条曲线的反向: 那边问「多久翻倍」, 这边
    问「多久减半」.)

    `created_at` 在未来 (时钟回拨 / 测试造数) 时按「就是现在」算 (封顶 1.0):
    负年龄会让分值**大于 1**, 于是「未来」的记忆比刚写的还靠前 —— 那是个说不通
    的排序.

    Args:
        created_at: 记忆的产生时刻 (带时区 —— 库里全是 TIMESTAMPTZ).
        now: 拿哪一刻当「现在」.
        half_life_days: 半衰期 (天); 越短衰减越快.

    Returns:
        float: 分值, 0 < score <= 1.0 (越大越新鲜).
    """
    age_days = max(0.0, (now - created_at).total_seconds() / _SECONDS_PER_DAY)
    return 0.5 ** (age_days / half_life_days)


def _alive() -> ColumnElement[bool]:
    """「这条记忆还在」—— 没被软删.

    单独一个函数 (与 `threads._visible` 同一条理由): 软删该被每一处检索遵守,
    条件散在各条查询里就有一处会漏 —— 而漏的表现是「作废的记忆又冒出来了」.
    """
    return memories.c.deleted_at.is_(None)


def _owned(tenant_id: str, user_id: str) -> ColumnElement[bool]:
    """「这条记忆是这个人的」—— 每个方法唯一一道门槛 (读写都要).

    写成组合条件而不是两个 `.where()`: 它表达的是**一件事** (归属), 调用处因此
    看不出「可以只带一半」的余地.
    """
    return (memories.c.tenant_id == tenant_id) & (memories.c.user_id == user_id)


class MemoriesRepository(PgRepository):
    """记忆表的读写口 (方法只覆盖当前真正要用的场景, 不铺满 CRUD).

    Args:
        database: 数据库入口 (默认 `PgDatabase()`, 从环境变量读连接串).
        capacity: 每个 (tenant, user) 的活记忆条数上限 (默认 50).
        half_life_days: 时间衰减的半衰期, 天 (默认 30).
    """

    def __init__(
        self,
        database=None,
        *,
        capacity: int = DEFAULT_CAPACITY,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    ) -> None:
        super().__init__(database)
        self._capacity = capacity
        self._half_life_days = half_life_days

    async def add(
        self,
        *,
        tenant_id: str,
        user_id: str,
        content: str,
        kind: MemoryKind,
        source_thread_id: str | None = None,
        source_run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Memory:
        """写一条记忆; 完全相同的 `content` 已存在时**只更新时间戳**, 不新增行.

        去重按 (tenant, user, content) 精确匹配 (「这句话记过了吗」): 用户把同一
        条偏好说第二遍, 库里该还是一条 —— 两条一模一样的事实只会让 `recall` 端出
        重复内容. 命中的那条刷新 `updated_at` (「又被说了一次」); 衰减与排序**不
        因此重置** (分值按 `created_at` 算, 见模块 docstring) —— 这是一处**已知
        边界**, 等有真实使用数据再决定要不要让重复提及也变新鲜.

        **替换型的 kind 会顶掉旧值** (C30): `style` / `nickname` 这类「同一时刻
        只该有一条」的记忆, 写入前先把同 `(tenant, user, kind)` 的活行软删
        (`deleted_at` = 此刻 —— 旧风格何时被换掉可查). 去重优先于替换: 完全相同的
        `content` 还是只刷新时间戳, 不产生「删一条再插一条」的无谓历史.

        写完之后顺手做**容量淘汰** (同一事务里): 活记忆超过 `capacity` 条时,
        分值最低的几条被软删 —— 「写到上限之上, 淘汰的是分值最低的」那句话就
        落在这一处.

        **另一处已知边界 (并发)**: 去重与淘汰都是「先查后写」, 没有唯一约束或表
        锁兜着 —— 同一个用户的两次 `add` 真并发时, 一模一样的 `content` 可能落成
        两行, 容量也可能短暂是 N+1. 单写者 (一次只跑一个对话) 的现状下够用; 并发
        压力真实存在时, 收紧的抓手是 (tenant, user, content) 的部分唯一索引.

        Args:
            tenant_id / user_id: 归属 (必填 keyword, 见模块 docstring).
            content: 一句话事实 (提炼后的) —— 长度上限由调用方 (remember 工具) 管.
            kind: 记忆层次 (见 MemoryKind).
            source_thread_id / source_run_id: 从哪段对话 / 哪次运行来 (可空).
            created_at: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            Memory: 新增的那一行 (或去重命中的既有行, 时间戳已刷新).
        """
        moment = created_at if created_at is not None else datetime.now(UTC)
        async with self._session() as session:
            existing = session.scalars(
                select(Memory)
                .where(_owned(tenant_id, user_id), _alive())
                .where(memories.c.content == content)
                .limit(1)
            ).first()
            if existing is not None:
                session.execute(
                    update(memories)
                    .where(memories.c.memory_id == existing.memory_id)
                    .values(updated_at=moment)
                )
                # 上面的 UPDATE 走的是 Core 语句, ORM 手里这个对象还揣着旧值
                # (autoflush 也关着) —— 显式刷一次, 让返回的行说真话
                session.refresh(existing)
                return existing
            if KIND_BEHAVIORS[kind].replaces_previous:
                # 替换型 (C30): 新值顶上之前, 先把同 kind 的活行全部软删 ——
                # 「同一时刻只留一条」在库里的样子. 只碰同 kind: 换风格不动称呼.
                session.execute(
                    update(memories)
                    .where(
                        _owned(tenant_id, user_id),
                        _alive(),
                        memories.c.kind == kind.value,
                    )
                    .values(deleted_at=moment)
                )
            memory = Memory(
                memory_id=uuid4().hex,
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind.value,
                content=content,
                source_thread_id=source_thread_id,
                source_run_id=source_run_id,
                created_at=moment,
                updated_at=moment,
                last_used_at=None,
                deleted_at=None,
            )
            session.execute(memories.insert().values(**self._params(memory)))
            self._prune(session, tenant_id, user_id, moment=moment)
            # 插入后再读一次: 让**数据库补的默认值**也出现在返回对象里
            # (时间列在库里有默认值, 由库填的才是真值; 与 threads.add 同一手法)
            return self._one(session, memory.memory_id)

    async def list_for_user(self, tenant_id: str, user_id: str) -> list[Memory]:
        """取回这个用户的**活记忆**, 按衰减分值从高到低排 (新的/新鲜的在前).

        排序在 Python 里做而不是写进 SQL: 分值是指数衰减, 进 SQL 就是一串
        `power(0.5, ...)` 表达式 (半衰期参数还得一路带进语句里), 而条数有上限
        (`capacity`, 默认 50) —— 拉回来排既简单, 又把这个口径留在可单测的
        `recency_score` 里.

        **不带分页**: 条数由容量淘汰兜着 (每个用户至多 capacity 条), 一次给全
        —— C13 的 `recall` 因此可以「不区分场景, 把还记得的全端上来」. 等哪天
        单个用户的记忆量真的逼近上限、全量返回开始挤占上下文, 再考虑加检索参数
        (那时才是向量/关键词匹配该出场的时候, 见 C13 的取舍).

        Args:
            tenant_id / user_id: 归属 (必填, 见模块 docstring).

        Returns:
            list[Memory]: 分值降序; 分值打平时按 (created_at, memory_id) 定序
            —— 让结果可复现 (不然并列的两条谁先谁后看数据库的心情).
        """
        moment = datetime.now(UTC)
        async with self._session() as session:
            rows = list(
                session.scalars(
                    select(Memory).where(_owned(tenant_id, user_id), _alive())
                )
            )
        return sorted(
            rows,
            key=lambda memory: self._rank_key(
                memory.memory_id, memory.created_at, now=moment
            ),
            reverse=True,
        )

    async def list_by_id_prefix(
        self, prefix: str, *, tenant_id: str, user_id: str
    ) -> list[Memory]:
        """按编号前缀找**活行** (0 / 1 / 多条都如实回, 由调用方判断) —— C30.

        `forget` 工具的「拿编号找人」就落在这里: 编号是 `memory_id` 的前 8 位,
        前缀匹配天然也收完整编号. **不在这里替调用方筛唯一性** —— 「命中多条」
        是调用方要当场拒绝并如实回话的情形, 仓储装作看不见反而危险 (可能删错行).

        Args:
            prefix: 编号前缀 (`memory_id` 的一段十六进制; 照抄 recall 的显示).
            tenant_id / user_id: 归属 (必填, 见模块 docstring).

        Returns:
            list[Memory]: 命中的活行 (按编号排序, 结果可复现).
        """
        async with self._session() as session:
            rows = session.scalars(
                select(Memory)
                .where(_owned(tenant_id, user_id), _alive())
                # autoescape: 前缀是模型给的字符串, 里头的 % / _ 按字面当字符 ——
                # 少了它, 一个 `%` 会命中全部 (工具层另有一道形状校验, 双保险)
                .where(memories.c.memory_id.startswith(prefix, autoescape=True))
                .order_by(memories.c.memory_id)
            )
            return list(rows)

    async def touch_used(
        self,
        memory_ids: Sequence[str],
        *,
        tenant_id: str,
        user_id: str,
        moment: datetime | None = None,
    ) -> int:
        """把这几条记忆的「最后被取回时刻」刷成当下 (C30).

        只记账, **不参与排序与淘汰** —— 现在的 recall 是全量返回, 「所有行同刷」
        对「用得多的排前面」没有区分度; 从今天起积累使用证据, 等 recall 能返回
        子集那天再把使用信号接进打分 (见模块 docstring 与 C13 的遗留记录).

        Args:
            memory_ids: 刷哪几条 (recall 刚取回的那批).
            tenant_id / user_id: 归属 (必填 —— 与其余写方法同一条纪律: 不给
                「按一堆 id 批量改」的口子).
            moment: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            int: 刷到了几行 (0 = 这些编号里没有本人的活行).
        """
        if not memory_ids:
            return 0
        stamp = moment if moment is not None else datetime.now(UTC)
        statement = (
            update(memories)
            .where(
                memories.c.memory_id.in_(list(memory_ids)),
                _owned(tenant_id, user_id),
                _alive(),
            )
            .values(last_used_at=stamp)
        )
        async with self._session() as session:
            return int(session.execute(statement).rowcount)

    async def soft_delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
        moment: datetime | None = None,
    ) -> bool:
        """作废一条记忆 —— **软删**: 行留着 (审计可查), 检索不再返回它.

        与 `threads.soft_delete` 同一条规矩: WHERE 里只有归属, **不含**「还没
        删过」—— 重删幂等 (再删一次是「还是删着的」, 返回 True 而不是「找不到」);
        代价是那一次的时刻会被覆盖 (为一个排查用的时间戳加一个 `coalesce` 不值).

        Args:
            memory_id: 作废哪一条.
            tenant_id / user_id: 归属 (必填 keyword, 见模块 docstring).
            moment: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            bool: 命中了行 True; False = 没有这条记忆, 或者它不是这个人的.
        """
        stamp = moment if moment is not None else datetime.now(UTC)
        statement = (
            update(memories)
            .where(memories.c.memory_id == memory_id, _owned(tenant_id, user_id))
            .values(deleted_at=stamp)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    def _rank_key(
        self, memory_id: str, created_at: datetime, *, now: datetime
    ) -> tuple[float, datetime, str]:
        """打分的排序键 —— **取回与淘汰共用的那一个口径**.

        单独一个方法而不是在两处各写一遍 key: 「谁排前面 / 谁先被淘汰」必须是
        同一条曲线 (difficulties #33 的要求), 两处各抄一份迟早会漂. 分量依次是
        (衰减分值, 产生时刻, 编号): 前两个决定大方向, 最后的编号给**并列**定序
        —— 同刻写下的两条谁先谁后必须可复现, 不然淘汰结果看数据库的心情.
        """
        return (
            recency_score(created_at, now=now, half_life_days=self._half_life_days),
            created_at,
            memory_id,
        )

    def _prune(
        self,
        session: Session,
        tenant_id: str,
        user_id: str,
        *,
        moment: datetime,
    ) -> None:
        """把超量的活记忆软删掉 (分值最低的几条).

        **分值最低 = 最老** (分值只由时间决定, 见模块 docstring): 按排序键升序
        取前 overflow 个 —— 与 `list_for_user` 的降序是同一个 `_rank_key` 的
        两个方向.

        退出条件是「活记忆条数 <= capacity」: 一次 add 最多多出一行, 所以正常
        情况下 overflow 是 1; 循环式的写法不需要 (上限是容量不是时间窗).
        """
        rows = session.execute(
            select(memories.c.memory_id, memories.c.created_at).where(
                _owned(tenant_id, user_id), _alive()
            )
        ).all()
        overflow = len(rows) - self._capacity
        if overflow <= 0:
            return
        evicted = [
            row.memory_id
            for row in sorted(
                rows,
                key=lambda row: self._rank_key(
                    row.memory_id, row.created_at, now=moment
                ),
            )[:overflow]
        ]
        session.execute(
            update(memories)
            .where(memories.c.memory_id.in_(evicted))
            .values(deleted_at=moment)
        )

    @staticmethod
    def _params(memory: Memory) -> dict[str, object]:
        """实体 → 插入参数 (放在这里而不是通用工具里: 只有本仓储写这张表).

        这张字典是「本表有哪些列」的一份镜像 —— 少几行的话, 以后谁给 `add`
        加一个参数时就会对着它找不到落点.
        """
        return {
            "memory_id": memory.memory_id,
            "tenant_id": memory.tenant_id,
            "user_id": memory.user_id,
            "kind": memory.kind,
            "content": memory.content,
            "source_thread_id": memory.source_thread_id,
            "source_run_id": memory.source_run_id,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
            "last_used_at": memory.last_used_at,
            "deleted_at": memory.deleted_at,
        }

    @staticmethod
    def _one(session: Session, memory_id: str) -> Memory | None:
        """按主键取一行 (返回实体而不是裸行, 上层拿到的就是有属性的对象)."""
        return session.get(Memory, memory_id)
