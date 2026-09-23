"""帧与运行的双向溯源: `run_id` 改名 `loop_id` + 两列新外键

Revision ID: 0004_frame_run_linkage
Revises: 0003_migration_audit_log
Create Date: 2026-09-23

一句话说明: 两张表里那个同名字段 (`run_id`) 说的其实是两件事 (ticket 22) ——
`charagent_checkpoints.run_id` 是「哪一次**循环执行**落下的」, `charagent_runs.run_id`
是「这是哪一行账」. 名字一样、意思两样, 而且两边没有任何一列能连起来. 本条迁移做三件:

1. **帧上那个改名 `loop_id`** (纯改名: 数据原样搬过去, 值不变). 老帧读回来时存储层
   的 JSON 键也会跟着改名 —— 那是 `checkpoint/utils/migrations.py` 的 `_v5_to_v6`
   (`SCHEMA_VERSION` 5 → 6), 与这里是一件事的两半.
2. **帧新增 `run_id`**: 指向 `charagent_runs.run_id` 的外键 —— 「这一帧属于哪一行账」.
   可空是**语义**, 不是偷懒: 没配记录层的进程 (框架 CLI 演示)、那一轮没记上账、老帧,
   三种都该是 NULL (指向一个不存在的行更糟). 删掉那一行时置 NULL (帧比账目行活得久).
3. **运行行新增 `last_checkpoint_id`**: 指向帧的外键 —— 这一段运行落的**最后一帧**,
   顺它的 `parent_id` 往回走就是本次运行落的每一帧 (「花了多少」与「当时它看到了
   什么」由此对到同一件事上). 帧先存在、行后建, 所以这一列没有时序问题.

**两张表之间的环**: 3 与 2 合起来让 `checkpoints ⇄ runs` 互为外键. 这是有意的
(两侧都要能直接查), 代价是建表顺序不再有拓扑解 —— SQLAlchemy 的 `create_all` 会把
环里的那条外键推迟成 ALTER (见 `db/schema.py` 那两列的注释), 而这里本来就是两条
`ALTER TABLE`, 不受影响.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_frame_run_linkage"
down_revision: str | None = "0003_migration_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 外键名按 `db/schema.py` 的命名约定写死 (fk_<表>_<列>_<被引用表>): 手写迁移里
# 不写死的话, 建出来的名字与 metadata 那边对不上 —— `compare_metadata` 那个用例
# (test_db_alembic) 会当成「代码与库不一致」而红.
_FK_CHECKPOINTS_RUN = "fk_charagent_checkpoints_run_id_charagent_runs"
_FK_RUNS_LAST_FRAME = "fk_charagent_runs_last_checkpoint_id_charagent_checkpoints"


def upgrade() -> None:
    """改名 + 两列 (数据都原地保留)."""
    op.alter_column(
        "charagent_checkpoints",
        "run_id",
        new_column_name="loop_id",
        existing_type=sa.String(128),
        existing_nullable=False,
        comment="哪一次循环执行存下的 (一次循环执行的几帧共享; 续跑沿用)",
    )
    op.add_column(
        "charagent_checkpoints",
        sa.Column(
            "run_id",
            sa.String(128),
            nullable=True,
            comment="归属记录层的哪一行账 (charagent_runs); NULL = 不属于任何一行 "
            "(没配记录层的进程 / 那一轮没记上账 / 老帧)",
        ),
    )
    op.create_foreign_key(
        _FK_CHECKPOINTS_RUN,
        "charagent_checkpoints",
        "charagent_runs",
        ["run_id"],
        ["run_id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "charagent_runs",
        sa.Column(
            "last_checkpoint_id",
            sa.String(128),
            nullable=True,
            comment="这一段运行落的**最后一帧** (快照); 顺它的 parent_id 往回走就是本次"
            "运行落的全部帧 —— NULL = 没配快照存储 (或一帧都没落成)",
        ),
    )
    op.create_foreign_key(
        _FK_RUNS_LAST_FRAME,
        "charagent_runs",
        "charagent_checkpoints",
        ["last_checkpoint_id"],
        ["checkpoint_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """倒回去: 两列下岗, 那个名字改回来 (顺序与 upgrade 相反)."""
    op.drop_constraint(_FK_RUNS_LAST_FRAME, "charagent_runs", type_="foreignkey")
    op.drop_column("charagent_runs", "last_checkpoint_id")
    op.drop_constraint(_FK_CHECKPOINTS_RUN, "charagent_checkpoints", type_="foreignkey")
    op.drop_column("charagent_checkpoints", "run_id")
    op.alter_column(
        "charagent_checkpoints",
        "loop_id",
        new_column_name="run_id",
        existing_type=sa.String(128),
        existing_nullable=False,
        comment="是哪一次运行存下的",
    )
