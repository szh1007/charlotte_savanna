"""幂等存储的 PG 实现: 真库用例 (标 `pg_db`, 默认排除).

场景 → 断言:
- 三态转换: 首次认领 = CLAIMED (拿到执行权); 未释放的重复认领 = IN_PROGRESS
  (不得重复执行); complete 之后认领 = COMPLETED 且带既有结果;
- **并发**: 两个认领同时抢同一把键, 恰好一个拿到 CLAIMED —— 这条要的是「原子性
  落在数据库上」, 而不是「先查后插」(后者在并发下会双双开跑);
- release: 放行失败动作的重试; 已完成的不动 (结果不能被后来的失败抹掉);
- 补登: complete 在没有任何认领记录时也能写 (动作已落地、只是记录缺失的兜底);
- **过期** (内存实现没有的那一半): 过期之后同一把键可以重新认领, 没过期时不行;
  过期的 COMPLETED 行重来一次时不带旧结果; 没配 ttl 的键永不过期;
- 配置: ttl 配成 0 / 负数在造对象时就报错 (配错了却装上, 这个存储会安静地什么都
  挡不住 —— 那是最难发现的一种坏).

隔离做法与 `test_db_store.py` 一致: 独立 schema (`charagent_test`), 用完整个删掉
—— 开发库的 public 一个字节都不动.

**为什么过期那几条要注入时钟**: 真等 60 秒的用例没人会跑第二遍, 而「ttl 设成 0.1
秒再 sleep 一下」是拿真实等待换一个不稳定的断言. 时钟本来就是注入缝 (#61 的惯例),
而过期判定恰恰是「现在几点」的函数 —— 固定时钟一推, 过期与否就是确定的事.

**末尾两条 (ttl 配错 / 构造不连库) 其实不碰库**, 仍留在这个文件里: 它们测的是同一个
类的构造期行为, 而这一整套用例本就只在 `-m pg_db` 那一趟跑 (没有真库时, 这个存储
的其余部分一个字都测不了) —— 拆出去只是多一个文件, 覆盖的还是同一趟.
"""

from __future__ import annotations

import asyncio

import pytest
from doubles import FakeClock

from CharAgent.db import DataConfigError, PgDatabase, PgIdempotencyStore
from CharAgent.retry import ClaimResult, ClaimStatus, IdempotencyKey

pytestmark = pytest.mark.pg_db

# 固定时钟的起点 (2023-11-14T22:13:20Z): 数字本身没有意义, 取它只是为了断言
# 里那些「加 59 秒 / 加 61 秒」好读
T0 = 1_700_000_000.0


# ---------------------------------------------------------------------------
# 三态转换: 认领 / 完成 / 释放 (与内存实现同语义)
# ---------------------------------------------------------------------------


async def test_first_claim_wins_and_duplicate_claim_is_in_progress(
    db: PgDatabase,
) -> None:
    """首次认领拿到执行权; 未释放前的重复认领 = IN_PROGRESS (不得重复执行)."""
    store = PgIdempotencyStore(db)
    key = IdempotencyKey.generate()

    first = await store.claim(key)
    second = await store.claim(key)

    assert first.claimed is True
    assert first.status is ClaimStatus.CLAIMED
    assert second.claimed is False
    assert second.status is ClaimStatus.IN_PROGRESS
    assert second.result is None


async def test_completed_claim_returns_the_existing_result(db: PgDatabase) -> None:
    """完成后认领: COMPLETED + 既有结果 (重复请求直接返回, 不再执行 #17)."""
    store = PgIdempotencyStore(db)
    key = IdempotencyKey.generate()
    await store.claim(key)
    await store.complete(key, {"refund_id": "r-1", "status": "accepted"})

    again = await store.claim(key)

    assert again.claimed is False
    assert again.status is ClaimStatus.COMPLETED
    # 结果为 JSONB 存回: 形状原样 (字典还是字典), 不必调用方再解析一次
    assert again.result == {"refund_id": "r-1", "status": "accepted"}


async def test_release_allows_reclaim_after_failure(db: PgDatabase) -> None:
    """动作失败释放认领: 后续请求可重新认领 (不释放会把键永久卡死)."""
    store = PgIdempotencyStore(db)
    key = IdempotencyKey.generate()
    await store.claim(key)
    await store.release(key)

    again = await store.claim(key)

    assert again.claimed is True
    assert again.status is ClaimStatus.CLAIMED


async def test_release_does_not_erase_a_completed_result(db: PgDatabase) -> None:
    """release 只放行「在途」记录: 已完成的结果不能被抹掉 (防结果丢失)."""
    store = PgIdempotencyStore(db)
    key = IdempotencyKey.generate()
    await store.claim(key)
    await store.complete(key, "已受理")

    await store.release(key)

    kept = await store.claim(key)
    assert kept.status is ClaimStatus.COMPLETED
    assert kept.result == "已受理"


async def test_complete_records_result_without_prior_claim(db: PgDatabase) -> None:
    """complete 直接补登完成 (动作已落地但认领记录缺失时兜底, 不抛错)."""
    store = PgIdempotencyStore(db)
    key = IdempotencyKey.generate()

    await store.complete(key, "已受理")

    assert (await store.claim(key)).result == "已受理"


# ---------------------------------------------------------------------------
# 并发: 原子性真的落在数据库上
# ---------------------------------------------------------------------------


async def test_concurrent_claims_of_one_key_let_exactly_one_through(
    db: PgDatabase,
) -> None:
    """两个认领同时抢同一把键: 恰好一个 CLAIMED, 另一个 IN_PROGRESS.

    **为什么要各起一个事件循环**: 同一个循环里的两个协程在库里其实是**排队**的
    (`session.execute` 是同步调用, 它会占住循环, 另一个协程的语句根本插不进来) ——
    那样测出来的是「先来后到」, 不是「抢」. 各起一个循环 = 两条线程、两条连接上的
    两条语句真的同时在飞, 于是它们真在库里抢同一个主键.

    抢输的那一个有两种收场, 都该落到「有人在办」: 撞上还没提交的那一行 (被行锁
    挡住, 等对方提交后再看), 或者撞上已经提交的那一行 (冲突走 `DO NOTHING`).
    破的实现 (先查后插) 在这条下面的收场是「两边都以为自己是第一个」—— 多来几轮
    是为了让它没有侥幸的余地.
    """
    store = PgIdempotencyStore(db)

    for _ in range(5):
        key = IdempotencyKey.generate()
        results = await asyncio.gather(
            asyncio.to_thread(_claim_in_its_own_loop, store, key),
            asyncio.to_thread(_claim_in_its_own_loop, store, key),
        )

        winners = [result for result in results if result.claimed]
        assert len(winners) == 1, f"应当恰好一个拿到执行权, 实际: {results}"
        assert {result.status for result in results} == {
            ClaimStatus.CLAIMED,
            ClaimStatus.IN_PROGRESS,
        }


def _claim_in_its_own_loop(
    store: PgIdempotencyStore, key: IdempotencyKey
) -> ClaimResult:
    """在自己的线程与自己的事件循环里认领一次 (给并发用例当天平的一端)."""
    return asyncio.run(store.claim(key))


# ---------------------------------------------------------------------------
# 过期: 内存实现没有的那一半
# ---------------------------------------------------------------------------


async def test_an_expired_key_can_be_claimed_again(db: PgDatabase) -> None:
    """过期之后同一把键可以重新认领 (那已经不是同一次操作了); 没过期则不行."""
    clock = FakeClock(now=T0)
    store = PgIdempotencyStore(db, ttl_seconds=60, time_source=clock)
    key = IdempotencyKey.generate()

    assert (await store.claim(key)).status is ClaimStatus.CLAIMED

    clock.now += 59  # 还差一秒到期: 依然挡着
    assert (await store.claim(key)).status is ClaimStatus.IN_PROGRESS

    clock.now += 2  # 第 61 秒: 过期了
    assert (await store.claim(key)).status is ClaimStatus.CLAIMED


async def test_an_expired_completed_key_starts_over_without_the_old_result(
    db: PgDatabase,
) -> None:
    """过期的 COMPLETED 行被重新认领时, 旧结果**不能**被当成新一次的结果.

    这条是「过期 = 不是同一次操作」最要紧的那一面: 认领回来的必须是一张白纸
    (结果为空), 否则后来者会拿着上一手的结果去答复用户.
    """
    clock = FakeClock(now=T0)
    store = PgIdempotencyStore(db, ttl_seconds=60, time_source=clock)
    key = IdempotencyKey.generate()
    await store.claim(key)
    await store.complete(key, "上一手的结果")

    clock.now += 61
    again = await store.claim(key)

    assert again.status is ClaimStatus.CLAIMED
    assert again.result is None
    await store.complete(key, "这一手的结果")
    assert (await store.claim(key)).result == "这一手的结果"


async def test_without_a_ttl_the_key_never_comes_back(db: PgDatabase) -> None:
    """不配 ttl (默认): 永不过期 —— 时间再怎么走, 那把键都还作数.

    这条钉的是**默认口径**: 换到这个实现不该把保护范围悄悄改小 (内存实现也没有
    TTL), 要短命必须调用方明说.
    """
    clock = FakeClock(now=T0)
    store = PgIdempotencyStore(db, time_source=clock)
    key = IdempotencyKey.generate()
    await store.claim(key)
    await store.complete(key, "已受理")

    clock.now += 365 * 24 * 3600  # 一年以后
    again = await store.claim(key)

    assert again.status is ClaimStatus.COMPLETED
    assert again.result == "已受理"


# ---------------------------------------------------------------------------
# 配置: 配错了要在造对象那一刻报出来
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ttl", [0, -1.0])
def test_a_non_positive_ttl_is_rejected(ttl: float) -> None:
    """ttl 配成 0 / 负数: 构造期报错, 不把「装上了却什么都挡不住」带到运行期.

    0 与负数会让每一把键在认领的下一刻就又可以认领 —— 这个存储会安静地放过全部
    重放, 而这种坏法是排查时最费劲的一种 (它看起来完全正常).
    """
    with pytest.raises(DataConfigError, match="ttl_seconds 必须是正数"):
        PgIdempotencyStore(ttl_seconds=ttl)


def test_a_store_can_be_built_without_an_injected_database() -> None:
    """参数配对了就造得出来, 且**不连库** (连库是懒的).

    与上一条合起来说明: 配置错在**启动那一刻**就能报出来, 不必等第一次真去认领
    才发现 (那时挡不住的重放已经发生了).
    """
    assert isinstance(PgIdempotencyStore(ttl_seconds=60), PgIdempotencyStore)
