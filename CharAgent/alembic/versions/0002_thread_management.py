"""会话管理两列: 置顶时刻与删除时刻

Revision ID: 0002_thread_management
Revises: 0001_core
Create Date: 2026-09-23

一句话说明: 给 `charagent_threads` 补两个可空时间列, 让「置顶」「删除」两个
用户动作有地方落 —— 在此之前那张表**一个能表达它们的列都没有** (ticket 20).

这是 `0001_core` 之后的**第一条增量迁移** —— 迁移机制从此不再只是「初始那一次」:
压缩之后立的规矩 (脚本是历史, 只追加不改写) 从这里开始真正被执行.

两列各自为什么是**时刻**而不是布尔:

- `pinned_at`: 布尔能表达「置不置顶」, 表达不了「谁先置顶的」. 既然要加列, 就加
  撑得住排序语义的那一个 (`pinned_at DESC NULLS LAST` 天然把最近置顶的排最前).
  它与 `updated_at` 同型同义 (都是「某一刻发生了某事」), 排序表达式因此对称.
- `deleted_at`: 一列同时回答「删了没」(`IS NULL`) 与「何时删的」. 用布尔的话,
  「什么时候删的」就得再加一列 —— 而那个问题排查时一定会被问到.

**删除为什么是软删** (这一条决定了整个 L3 成本记账能不能信): 硬删会顺着外键把
`charagent_runs` / `charagent_messages` / `charagent_tool_calls` 一起 CASCADE 掉
(`db/schema.py` 的外键动作), 而成本记账正挂在 `runs` 表上 —— 硬删会让「上周花了
多少钱」凭空少一块. 帧也一样 (ticket 24 起 `charagent_checkpoints.thread_id` 也是
CASCADE): 那是「模型为什么忘了」唯一要查的东西.

而用户语义上的「删除」本来就是「从我的列表里消失」, 软删完全满足. 代价是需要有人
(或一条定时任务) 在很久之后真删掉 —— 本项目不做, 这条决定连同被否掉的替代方案
(硬删 / 只过滤不落列 / 复用 `status`) 记在 `CharApp/docs/adr/0007-*.md`.

两列都**可空且没有默认值**: 已有的行两列都是 NULL, 而 NULL 的语义正是「没置顶 /
没删」—— 于是这次迁移对老数据是**零改写** (只加列, 不 UPDATE, 不需要回填).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_thread_management"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "charagent_threads",
        sa.Column(
            "pinned_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="置顶时刻 —— NULL = 未置顶; 有值 = 置顶那一刻 (列表把置顶项排最前)",
        ),
    )
    op.add_column(
        "charagent_threads",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "删除时刻 —— NULL = 还在; 有值 = 已被用户删除 (软删: 行与消息都留着)"
            ),
        ),
    )


def downgrade() -> None:
    # 与 upgrade 相反的顺序 (先加的后删): 两条语句之间没有依赖, 但保持这条惯例
    # 是为了让「读一遍 downgrade 就知道 upgrade 干过什么」在更复杂的迁移里也成立
    op.drop_column("charagent_threads", "deleted_at")
    op.drop_column("charagent_threads", "pinned_at")
