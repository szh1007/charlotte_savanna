"""长期记忆表: 跨会话还有用的一句话事实

Revision ID: 0007_memories
Revises: 0006_run_usage_by_model
Create Date: 2026-10-05

一句话说明: 新建 `charagent_memories` —— 长期记忆的**持久化底座**
(difficulties #31-#33, MEM-D1; 此前 `db/README.md` 的追加路径里列作 P2).

**这张表解决什么**: 会话与快照只记得住「这段对话里发生了什么」; 用户上次说的
「以后都用简短回复」换一段对话就没了. 本表存的是**跨会话仍然有用的一句话事实**
—— 偏好与事实 (semantic) 或带时间戳的历史事件 (episodic), 按时间衰减排序取回.

四条写进结构的设计口径 (详见 `db/schema.py` 那张表与
`repositories/memories.py` 的注释):

1. **不挂外键**: 记忆比产生它的那段对话活得久 —— 挂 charagent_threads 的外键
   要么让删会话时把记忆 CASCADE 掉, 要么把它的来路 SET NULL 掉, 两个都不对.
   `source_thread_id` / `source_run_id` 只是溯源线索.
2. **存提炼后的事实**: 不是原文, 也不是每轮摘要 —— 「每轮摘要」的家已经在
   `charagent_messages` 的 `hidden=True` 行里 (MEM-D2), 再存一份就是双写.
3. **软删** (`deleted_at`): 与 #31「废弃用软删标记保留可追溯性」同源 —— 行
   留着, 任何检索都不再返回它. 容量淘汰写的也是这一列.
4. **归属两列 NOT NULL** (`tenant_id` / `user_id`): #32 的硬性安全要求 ——
   检索强制过滤, 不靠「相信模型不乱看」.

**规模**: 本迁移只加**一张表** (加索引一共两 DDL), 不碰任何既有表 —— 老数据
零改写, 也不需要回填.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_memories"
down_revision: str | None = "0006_run_usage_by_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charagent_memories",
        sa.Column(
            "memory_id",
            sa.String(length=128),
            nullable=False,
            comment="记忆编号 (uuid4 hex)",
        ),
        sa.Column(
            "tenant_id",
            sa.String(length=128),
            nullable=False,
            comment="租户编号 —— 多租户隔离的过滤键 (#32): 检索强制带上, "
            "不靠「相信模型不乱看」",
        ),
        sa.Column(
            "user_id",
            sa.String(length=128),
            nullable=False,
            comment="记忆属主 —— 谁说的记给谁, 检索按它过滤",
        ),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            comment="记忆层次 (MemoryKind): episodic = 带时间戳的历史事件 "
            "(「上次说他换了工作」), semantic = 偏好与事实 (「偏好简短回复」)",
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            comment="一句话事实 —— **提炼后的** (不是原文, 也不是每轮摘要): "
            "「跨会话仍然有用」才值得写进这一行",
        ),
        sa.Column(
            "source_thread_id",
            sa.String(length=128),
            nullable=True,
            comment="这条记忆从哪段对话来 (溯源线索, 刻意不做外键: 会话删了, "
            "记忆还该活着); NULL = 没记来路",
        ),
        sa.Column(
            "source_run_id",
            sa.String(length=128),
            nullable=True,
            comment="这条记忆从哪次运行来 (与 source_thread_id 同一条规矩: "
            "只是线索, 不做外键)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="产生时刻 —— **时间衰减按它起算** (越新分值越高)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="最后更新时刻 —— 同一句话被重复写入时刷新它 (去重, 不新增行)",
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最后一次被检索取回的时刻 —— 给「用得多的排前面」留的口; "
            "NULL = 还没被用过",
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="作废时刻 —— NULL = 还在; 有值 = 已作废 (软删: 行留着, "
            "任何检索都不再返回它). 容量淘汰写的也是这一列",
        ),
        sa.PrimaryKeyConstraint("memory_id", name="pk_charagent_memories"),
        comment="长期记忆: 跨会话还有用的一句话事实 (偏好 / 历史事件) —— "
        "按时间衰减排序取回, 超量淘汰分值最低的",
    )
    op.create_index(
        "ix_charagent_memories_tenant_user",
        "charagent_memories",
        ["tenant_id", "user_id"],
    )


def downgrade() -> None:
    # 退回去就是把记忆整张丢掉 (它没有外键, 也没有任何表引用它). 代价是长期记忆
    # 整体消失 —— 回退前要清楚这一点 (但记忆是**可再积累**的: 丢了不伤账目,
    # 与 runs 那类账本不同).
    op.drop_index("ix_charagent_memories_tenant_user")
    op.drop_table("charagent_memories")
