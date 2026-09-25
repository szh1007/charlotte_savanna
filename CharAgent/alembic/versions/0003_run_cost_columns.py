"""运行成本两列: 金额改成收尾写死 + 明细列

Revision ID: 0003_run_cost_columns
Revises: 0002_thread_management
Create Date: 2026-09-25

一句话说明: 给 `charagent_runs.total_cost` **换一条注释 + 改成可空**, 并新增
`total_cost_detail` —— 金额从此在**运行收尾那一刻**算好写死, 明细记下这笔钱是怎么
算出来的 (ticket 28; 口径的来龙去脉见 `db/cost.py` 的模块 docstring).

**为什么改成可空**: 收尾时算不出来是**正常结局之一** (没配价目表 / 上游没上报某一档
分量 / 中国日历里没有那一年的数据). 这时候那一列该是 NULL —— 「没算出来」与「真的
花了 0 元」是相反的结论, 而这一列原先是 NOT NULL DEFAULT 0, 两者分不开. 这与那五列
用量分解已经是同一条规矩 (它们的注释里就写着「NULL 与 0 是两回事」).

**为什么要一条明细列**: 只留一个数字的话, 事后没人能验算, 连「这笔是按峰价还是谷价
算的」都看不出来 —— 而峰谷是本次运行的属性 (判据是它的开始时刻), 不写下来无从复原.

**为什么这是「改」而不是「加一条新迁移」**: `0003` 是**本片自己**的迁移, 还没提交、
也没进过任何别人的库 (ADR-0006 那条「迁移历史在发布前可以压」的先例); 而在它里面
把注释再改一次、顺便可空 + 加列, 比留下「0003 说 A / 0004 说非 A」两条互相打架的
历史干净得多. 已经跑过旧 0003 的本机开发库用 `downgrade base` + `upgrade head`
重来一次即可 (那一版只动过注释, 且库里当时是空的).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_run_cost_columns"
down_revision: str | None = "0002_thread_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 旧的列状态 (回退时写回去). 与 0001_core.py 里那一列建表时给的一致.
_OLD_COMMENT = "本 run 累计花费 (金额用 NUMERIC 不用浮点: 浮点算钱会丢分)"

# 新的注释: 与 db/schema.py 的 `total_cost` 那一列逐字一致 (改一处必须改两处)
_NEW_COMMENT = (
    "本次运行的花费 —— 收尾那一刻按当时的价目表算好写入 "
    "(NULL = 没算出来, 原因见 total_cost_detail; 0 = 真的花了 0 元); "
    "写进去之后不再改写; 金额用 NUMERIC 不用浮点: 浮点算钱会丢分"
)

_DETAIL_COMMENT = (
    "这笔钱的来路 (与 total_cost 同生共死): 算得出来时记哪一套价 "
    "(peak / valley) + 三个单价 + 三档用量; 算不出来时记原因 (没配价 / 缺哪个"
    "分量 / 日历过期). 只留一个数字的话事后没人能验算, 连「这是峰价还是谷价"
    "算的」都看不出来 (ticket 28)"
)


def upgrade() -> None:
    op.alter_column(
        "charagent_runs",
        "total_cost",
        # existing_* 两个参数是**给 autogenerate 比对用的输入**, 不是「照原样保留」
        # 的指令 (alembic 只在给了 `server_default` / `type_` 时才动那两样) —— 所以
        # 下面这一句做的是**三件事**: 丢掉默认值、放开 NOT NULL、换注释.
        #
        # 默认值必须显式丢掉: 留着 `DEFAULT 0` 的话, 任何漏写这一列的 INSERT 都会
        # 得到 0, 而 0 在这一列里的意思是「真的花了 0 元」—— 正是本片要分开的那件事.
        # 而这道差异 `alembic check` 抓不到 (env.py 没开 compare_server_default),
        # 于是它只能靠这里写对 (下面那条用例把默认值也断言上了).
        existing_type=sa.Numeric(14, 6),
        existing_server_default=sa.text("0"),
        server_default=None,
        nullable=True,
        comment=_NEW_COMMENT,
    )
    op.add_column(
        "charagent_runs",
        sa.Column("total_cost_detail", JSONB(), nullable=True, comment=_DETAIL_COMMENT),
    )


def downgrade() -> None:
    op.drop_column("charagent_runs", "total_cost_detail")
    # 回退成 NOT NULL 之前必须先把空值填掉, 否则 Postgres 会直接拒绝这次 ALTER
    # (「column contains null values」). 填 0 是那时候唯一能表达的值, 也正好是
    # 老代码读这一列时期待的默认值
    op.execute("UPDATE charagent_runs SET total_cost = 0 WHERE total_cost IS NULL")
    op.alter_column(
        "charagent_runs",
        "total_cost",
        existing_type=sa.Numeric(14, 6),
        nullable=False,
        server_default=sa.text("0"),
        comment=_OLD_COMMENT,
    )
