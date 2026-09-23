"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

一句话说明这次迁移做什么 (给后来翻迁移历史的人看):
它解决什么问题、为什么现在改、有什么要留意的.

（这一段的**首段**会被 alembic 的 Script.doc 取走, 由 env.py 的钩子写进
`charagent_migrations.name` —— 所以 `${message}` 那一行就是库里看到的标题.）
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
