"""幂等登记簿的柜员: 认领 / 完成 / 释放, 落在 `charagent_idempotency_keys` 表上.

一句话理解: `retry/` 说了幂等凭证长什么样、认领有哪几态, 本文件把它**真的存进
库** —— 于是「这个动作做过了吗」跨得了进程: 关掉服务、换一个进程再问一次, 答案
还在 (进程内那份 dict 做不到这件事, 而 HITL 的两次恢复正是跨进程的).

**为什么归 db 包** (而不是写在 `retry/` 里): `retry/` 的 import 只有标准库与它
自己的 utils —— 那是「零数据库依赖」这条边界, 让它保持原样, 需要用库的人自己来
db 取. 依赖方向因此是单向的: db → retry (本文件 import 键与两个静态类型), retry
不知道 db 存在.

**认领为什么是一条 SQL 而不是「先查后插」**: 那两步之间有时间窗口 —— 两个人同时
认领同一把键, 两边都查到「没有记录」, 然后都去执行. 写成一条带冲突处理的 INSERT
(`ON CONFLICT (key) ... RETURNING`), 数据库用唯一索引保证只有一个能插进去, 另一个
自然拿不到执行权. 这就是「抢到的那个人负责」(`db/repositories/runs.py` 的
`try_transition` 是同一个道理, 那边靠的是带条件的 UPDATE).

**过期为什么挤在认领这一条语句里判**: 同一把键**过期之后可以重新认领** (那时它
已经不是同一次操作了), 于是「那行过没过期」只在认领时被问一次 —— 条件写在
`ON CONFLICT` 的 `DO UPDATE ... WHERE` 上: 过期的行被这次认领整个改写成新的一次
操作, 没过期的行一动不动 (这把键的答案留在原地).

**表结构为什么长这样** (键是身份 / 状态只有两态 / 结果可空): 见 `db/schema.py`
那张表的注释, 本文件只管怎么用 SQL 把它转起来.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from CharAgent.db.errors import DataConfigError
from CharAgent.db.repositories.base import Database, PgRepository
from CharAgent.db.schema import idempotency_keys
from CharAgent.retry.idempotency import IdempotencyKey
from CharAgent.retry.utils.types import ClaimResult, ClaimStatus


class PgIdempotencyStore(PgRepository):
    """幂等存储的 Postgres 实现 (形状即 `retry/` 的 `IdempotencyStore` 协议).

    三个动作与内存实现同语义 (那条语义由 `retry/idempotency.py` 定义):

    | 动作 | 做什么 | 数据库层怎么落 |
    |------|--------|----------------|
    | `claim` | 拿到才执行 | 一条 `INSERT ... ON CONFLICT` + `RETURNING` |
    | `complete` | 记下结果 | 有行就改, 没行就补插一行 (兜底同内存实现) |
    | `release` | 失败放行重试 | 把**在途**那一行删掉 (已完成的不动) |

    三个动作里容易记错的是「谁该是原子的」: 只有 `claim` 要跟别人抢 (它一次语句
    里既查又写, 判据全交给数据库), `complete` / `release` 是收尾动作 —— 它们只
    跟**自己那一次执行**有关, 不需要抢.

    **已知边界 (owner 校验没做)**: 同一把键在过期后被重新认领时, 前一手动作的
    `complete` 会写进后来那一手的行 —— 两次执行的结果因此可能互相覆盖. 挡它要靠
    「谁认领的」这个身份 (存储协议里现在没有这个概念, 属 P1 的 Redis 版), 而本框架
    给这把键配的 TTL 远长于一次工具调用的耗时, 这条路走不到.

    Args:
        database: 数据库入口 (默认 `PgDatabase()`, 从环境变量读连接串).
        ttl_seconds: 认领之后多少秒算过期; None (默认) = 永不过期. **默认不过期是
            有意的**: 内存实现也没有 TTL, 默认值若短命, 换成这个实现就成了「保护
            范围被悄悄改小」—— 要短命得调用方明说.
        time_source: 「现在几点」的注入点 (默认 `time.time`, 单位秒). 注意它**不是**
            `RetryPolicy.time_source` 那种单调计时器: 这里要的是墙上时钟 (比的是库里
            那个绝对时刻), 返回 `float` 只是沿用同一条注入惯例 (#61 确定性),
            测试替身 `FakeClock` 给的也是 float.

    Raises:
        DataConfigError: `ttl_seconds` 不是正数.
    """

    def __init__(
        self,
        database: Database | None = None,
        *,
        ttl_seconds: float | None = None,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(database)
        if ttl_seconds is not None and ttl_seconds <= 0:
            # 0 与负数会让**每一把键**在认领的下一刻就又可以认领 —— 这个存储接上
            # 了却什么都挡不住, 而它不会报任何错. 配置错要在造对象时就报出来
            # (对齐 RetryPolicy.__post_init__ 那条 fail fast)
            raise DataConfigError(
                f"ttl_seconds 必须是正数或 None (不过期): 实际 {ttl_seconds!r} —— "
                "0 与负数等于每把键认领完立刻可被重新认领, 即这个存储挡不住任何重放"
            )
        self._ttl = ttl_seconds
        self._time_source = time_source

    async def claim(self, key: IdempotencyKey) -> ClaimResult:
        """认领 (#17): 首次拿到执行权, 重复请求被挡回在途 / 已完成状态.

        一整行 `claim` 的判定都压在那条 `ON CONFLICT` 上 —— 应用层不查、不判断,
        于是两个人同时来也只有一个人能拿到 `CLAIMED`.
        """
        moment = self._now()
        # 一次认领 = 这一行的全部内容 (状态在途 / 结果清空 / 有效期从头算). 改写
        # 过期行用的是**同一份值**: 那一行从此描述的就是新的一次操作, 不该留半点
        # 上一手的痕迹 —— 尤其不能留下上一手的结果 (否则后来者会把它当成自己的)
        record = {
            "status": ClaimStatus.IN_PROGRESS.value,
            "result": None,
            "expires_at": self._expires_at(moment),
            "created_at": moment,
            "updated_at": moment,
        }
        statement = (
            pg_insert(idempotency_keys)
            .values(key=key.value, **record)
            .on_conflict_do_update(
                index_elements=[idempotency_keys.c.key],
                set_=record,
                # 只有**已经过期**的那一行允许被这次认领改写. NULL (永不过期) 与
                # 未来时刻都不满足它, 于是没过期的行一动不动: 那把键要么被挡回
                # 「有人在做」, 要么直接返回既有结果
                where=idempotency_keys.c.expires_at <= moment,
            )
            .returning(idempotency_keys.c.key)
        )
        async with self._session() as session:
            if session.execute(statement).first() is not None:
                # 插进去了 / 改写了过期行 —— 这次由我执行
                return ClaimResult(status=ClaimStatus.CLAIMED)
            row = session.execute(
                select(idempotency_keys.c.status, idempotency_keys.c.result).where(
                    idempotency_keys.c.key == key.value
                )
            ).first()
        # 走到这里说明上面那条改写没通过 —— 也就是那一行要么永不过期、要么还在
        # 有效期内, 于是下面读到的东西没有「过期了但看起来还作数」的余地
        if row is not None and row.status == ClaimStatus.COMPLETED.value:
            return ClaimResult(status=ClaimStatus.COMPLETED, result=row.result)
        # 其余一律当作「有人在办」: 这是**安全**的那个答复 (不会让调用方再执行
        # 一次). 真落到这里而其实没人办 (比如那一行刚被 release 掉) 也不冤枉 ——
        # 释放意味着前一次失败了, 调用方本来就该自己决定要不要重来一遍
        return ClaimResult(status=ClaimStatus.IN_PROGRESS)

    async def complete(self, key: IdempotencyKey, result: Any = None) -> None:
        """登记完成与结果 (动作成功收尾).

        没有认领记录时**补插一行** (与内存实现同一条兜底: 动作已经落地、只是记录
        缺失, 这时报错反而会让一次成功的动作看起来像失败).

        已有那一行时只改状态 / 结果 / `updated_at` 三样 —— `expires_at` 与
        `created_at` 保持认领那一刻写下的值 (有效期从认领算起, 见 `db/schema.py`
        那一列的注释).

        Args:
            key: 幂等键.
            result: 要记下的结果, 必须可 JSON 序列化 (它进 JSONB 列).
        """
        moment = self._now()
        statement = (
            pg_insert(idempotency_keys)
            .values(
                key=key.value,
                status=ClaimStatus.COMPLETED.value,
                result=result,
                # 这两列只在「补插」那条路上用得上 (没有认领记录时才写)
                expires_at=self._expires_at(moment),
                created_at=moment,
                updated_at=moment,
            )
            .on_conflict_do_update(
                index_elements=[idempotency_keys.c.key],
                set_={
                    "status": ClaimStatus.COMPLETED.value,
                    "result": result,
                    "updated_at": moment,
                },
            )
        )
        async with self._session() as session:
            session.execute(statement)

    async def release(self, key: IdempotencyKey) -> None:
        """释放**在途**认领 (动作失败, 允许后续合法重试).

        释放就是把这一行删掉 —— 与内存实现同语义 (那边也是 `del`): 「没人认领过」
        与「认领被撤回了」对后来的请求是同一种东西, 都是「可以执行」.

        已完成的行**不动**: 结果不能被后到来的失败抹掉 (#17 幂等语义的核心),
        而状态那一列正好把这两种行分开.
        """
        async with self._session() as session:
            session.execute(
                delete(idempotency_keys)
                .where(idempotency_keys.c.key == key.value)
                .where(idempotency_keys.c.status == ClaimStatus.IN_PROGRESS.value)
            )

    def _now(self) -> datetime:
        """现在几点 (走注入的时间源) —— 带时区的 UTC 时刻, 直接进 TIMESTAMPTZ 列."""
        return datetime.fromtimestamp(self._time_source(), tz=UTC)

    def _expires_at(self, moment: datetime) -> datetime | None:
        """这一行什么时候过期; None = 不过期 (没配 ttl)."""
        if self._ttl is None:
            return None
        return moment + timedelta(seconds=self._ttl)
