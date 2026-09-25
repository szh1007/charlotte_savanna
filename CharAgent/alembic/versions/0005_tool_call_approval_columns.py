"""挂起态补两列: 给用户看的那句话与还缺什么

Revision ID: 0005_tool_call_approval_columns
Revises: 0004_idempotency_keys
Create Date: 2026-09-25

一句话说明: 给 `charagent_tool_calls` 加 `approval_prompt` 与 `approval_needs`
两列 (issue 34, 依据 ADR-0014).

**为什么加在这张表上**: ADR-0014 已经定下「挂起不建审批表」—— 一次挂起的全部状态
由**那次工具调用自己那一行**表达 (`status = needs_approval` + `approved_by` /
`approved_at`). 而挂起还有一个东西要记: **问用户什么**. `Decision.requires_approval`
带两样业务语义 (`prompt` 话术 + `needs` 缺什么, 框架只搬运不解释), 它们在挂起那一刻
就得落库 —— 因为**刷新页面之后要重建那张确认卡**, 而前端读的是记录表, 挂起态读的是
这张表, 两者不在同一条读取路径上.

**为什么不塞进既有的列**: `result` 装的是「这次调用跑出什么」, 挂起时它恰好是空的
(还没跑), 而恢复那一段会把它推进成真正的结果 —— 两件事抢一列, 收尾那一写就会把
话术冲掉. `arguments` 更不行: 那是**模型填的参数**, 与「我们要问用户什么」不是同一
件事 (ADR-0015 的「密码永不进参数」正是靠这条区分).

**可空**: 绝大多数工具调用不是挂起 (NULL = 不是要人批的那种). 两列都只在那一条
挂起的调用上写值, 与 `approved_by` / `approved_at` 同一族.

**回填**: 不需要 —— 这两列是**新的**事实 (以前根本没有挂起态的生产者), 老行留 NULL
即是如实 (那时确实没有要问的话).

列注释与 `db/schema.py` 逐字一致 (两边不一致时 `--autogenerate` 每次都会报「注释变了」,
那些噪音会淹掉真正的 schema 变更).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_tool_call_approval_columns"
down_revision: str | None = "0004_idempotency_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "charagent_tool_calls",
        sa.Column(
            "approval_prompt",
            sa.Text(),
            nullable=True,
            comment="挂起时**给用户看的那句话** (#25, 业务给的话术, 框架只搬运): "
            "刷新页面之后靠它重建确认卡 —— 缺了它, 用户看到一张没有字的卡, "
            "永远不知道该确认什么. NULL = 这一条不是要人批的调用",
        ),
    )
    op.add_column(
        "charagent_tool_calls",
        sa.Column(
            "approval_needs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="挂起时**还缺什么** (#25, 机器可读的短名字数组, 如 "
            '["payment_password"]): 前端按它决定卡片上要不要渲染输入框. '
            "框架不解释这些名字的含义, 原样透传; NULL = 这一条不是要人批的调用",
        ),
    )


def downgrade() -> None:
    # 退回去就把这两列丢掉: 老代码不知道挂起态, 留着它们只会是两列没人读的数据.
    # 代价是**未决挂起的确认卡在刷新之后重建不出来** (前端拿不到要问什么), 所以
    # 回退前先确认没有正挂在等人的运行 (`SELECT count(*) FROM charagent_tool_calls
    # WHERE status = 'needs_approval' AND approved_at IS NULL`)
    op.drop_column("charagent_tool_calls", "approval_needs")
    op.drop_column("charagent_tool_calls", "approval_prompt")
