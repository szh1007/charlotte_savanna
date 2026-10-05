"""dw 连接的只读与语句预算 (C16) —— 验的是**生产 `dw_client` 本人**.

它需要本地 MySQL (端口 3307) 与本地私有 `conf/app_config.yaml`; 两者缺席就 skip
(干净机器上「clone 后能跑」不受影响), 本机的实测输出记在 C16 票据的实施记录里.

这一层是数据库侧兜底: 语句级白名单万一漏了, 写操作由 MySQL 报 1792 拦下;
慢查询在 `max_execution_time` 内被掐断 (3024), 且**连接仍可用** —— 校正节点
还能接着走.
"""

from __future__ import annotations

import socket
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.clients.mysql import dw_client
from app.conf.app_config import app_config


def _mysql_reachable() -> bool:
    if not app_config.db_dw.host or app_config.db_dw.port <= 0:
        return False  # 干净机器上的配置替身
    try:
        with socket.create_connection(
            (app_config.db_dw.host, app_config.db_dw.port), timeout=1.0
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _mysql_reachable(), reason="需要本地 dw MySQL (干净机器上正常跳过)"
)


async def test_dw_session_is_read_only_and_time_budgeted() -> None:
    dw_client.init()
    try:
        async with dw_client.session() as session:
            row = (
                await session.execute(
                    text("SELECT @@transaction_read_only, @@max_execution_time")
                )
            ).fetchone()
            assert row == (1, app_config.dw_guard.statement_timeout_ms), (
                "连接建立时钉的只读与语句预算没生效"
            )
            await session.rollback()

            with pytest.raises(OperationalError) as write_attempt:
                await session.execute(text("CREATE TEMPORARY TABLE probe_t (i int)"))
            assert write_attempt.value.orig.args[0] == 1792, "写操作该被只读事务拦下"
            await session.rollback()

            # 锁定读也是写语义 (会拿锁) —— 只读事务一并拒掉, 白名单之外的覆盖点
            with pytest.raises(OperationalError) as locking_read:
                await session.execute(text("SELECT 1 FROM fact_order FOR UPDATE"))
            assert locking_read.value.orig.args[0] == 1792
            await session.rollback()

            # 语句预算: 用 hint 把这一条压到 200ms (会话级是 10s, 不然测试要等 10s)
            heavy = (
                "SELECT /*+ MAX_EXECUTION_TIME(200) */ SUM(a.order_amount) "
                "FROM fact_order a JOIN fact_order b JOIN fact_order c "
                "JOIN fact_order d"
            )
            started = time.perf_counter()
            with pytest.raises(OperationalError) as slow_query:
                await session.execute(text(heavy))
            elapsed = time.perf_counter() - started
            assert slow_query.value.orig.args[0] == 3024, "慢查询该被掐断"
            assert elapsed < 5, f"掐断来晚了: {elapsed:.1f}s"
            await session.rollback()

            # 被掐断的是语句不是连接: 之后照常查询 (校正节点要靠这条)
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
    finally:
        await dw_client.close()
