"""迁移审计表: 每应用一条迁移追加一行

Revision ID: 0003_migration_audit_log
Revises: 0002_run_usage_breakdown
Create Date: 2026-09-23

一句话说明: `charagent_alembic_version` 只记**当前 head** —— 那是 alembic 的设计
(它靠那一行决定还该跑哪些迁移; 多行会被当成**分叉的 head**, 后续 upgrade 直接报
错), 于是「这张库什么时候上的哪条迁移」在库里查不到. 本表把这件事补上: 每应用一条
迁移追加一行 (编号 + 标题 + 时刻 + 执行者), 由 `alembic/env.py` 的
`on_version_apply` 钩子写 —— **与迁移在同一个事务里**, 要么都成要么都不成. 为什么不
手工记: 手工一定会漏, 而漏掉的那一次在库里没有任何痕迹.

**0001 / 0002 是补记**: 本表由 0003 建起来, 那两条的 `applied_at` / `applied_by`
在**任何**库里都无从考证 (新建库里它们同样发生在建表之前), 一律留 NULL —— 编一个
时间比留空更糟. 钩子对「审计表还不存在」的那几步安静跳过 (见 `env.py`), 所以补记
只发生在这里一次.

**标题是硬编码的**: 迁移脚本是**冻住的历史**, 不许在运行时去读别的迁移文件的
docstring (那等于让历史随代码变). 下面那两句与 0001 / 0002 脚本 docstring 的首行
逐字一致 —— 那两份脚本本身也是冻住的, 不会漂.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_migration_audit_log"
down_revision: str | None = "0002_run_usage_breakdown"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 补记的两条 (编号, 标题); 时刻与执行者留 NULL, 理由见模块 docstring
_BACKFILL: tuple[tuple[str, str], ...] = (
    (
        "0001_core",
        "五实体核心表: threads / runs / messages / tool_calls / checkpoints",
    ),
    ("0002_run_usage_breakdown", "runs 表加用量分解五列 (成本归因 #34)"),
)

# 补记那一句 (与钩子写的是同一个形状, 只是时刻与执行者留空)
_BACKFILL_SQL = sa.text(
    "insert into charagent_migrations (revision, name, applied_at, applied_by)"
    " values (:revision, :name, null, null)"
    " on conflict (revision) do nothing"
)


def upgrade() -> None:
    """建审计表, 再把建表之前那两条补记进去.

    列定义与 `db/schema.py` 的 `migrations` 表**逐字一致** (类型 / 可空性 / 注释)
    —— `compare_metadata` 逐列比, 注释不一致也会报成差异.
    """
    op.create_table(
        "charagent_migrations",
        sa.Column(
            "revision",
            sa.String(length=64),
            nullable=False,
            comment="迁移编号 (alembic 的 revision, 如 0002_run_usage_breakdown)",
        ),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
            comment=(
                "迁移标题 (脚本 docstring 的首行, 与 alembic history 印的是同一句)"
            ),
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="应用时刻 (带时区); NULL = 补记 (见表注释)",
        ),
        sa.Column(
            "applied_by",
            sa.String(length=128),
            nullable=True,
            comment="执行者 (user@host); NULL = 补记 (见表注释)",
        ),
        sa.PrimaryKeyConstraint("revision", name="pk_charagent_migrations"),
        comment="迁移审计: 每应用一条迁移追加一行. 0003 之前的两条 (0001 / 0002) "
        "由 0003 补记, 时刻与执行者留 NULL —— 它们在**任何**库里都发生在建表之前, "
        "无从考证, 编一个时间比留空更糟",
    )

    for revision_id, name in _BACKFILL:
        # on conflict do nothing: 新建库里这两条本可能由钩子记过 —— 钩子对「表还不
        # 存在」的步骤是跳过的, 所以正常情况下不会重复; 这一句是防手滑重跑的兜底
        op.execute(_BACKFILL_SQL.bindparams(revision=revision_id, name=name))


def downgrade() -> None:
    """删掉审计表 (连带里面所有记录).

    纯减法: 撤了就没有「哪条迁移什么时候上的」这份档案了 —— 业务表一根毫毛都不动.
    """
    op.drop_table("charagent_migrations")
