"""runs 表加用量分解五列 (成本归因 #34)

Revision ID: 0002_run_usage_breakdown
Revises: 0001_core
Create Date: 2026-09-23

一句话说明: `charagent_runs` 多了五列 —— `input_tokens` / `output_tokens` /
`reasoning_tokens` / `cache_hit_tokens` / `cache_miss_tokens`. 它们与既有的
`total_tokens` 是**同一个数的两种记法**: 那个是账单原值 (上游 usage 的总数, 也是
唯一能跟供应商对账的数), 这五个是它的拆解, 用来回答「钱花在哪一类 token 上」——
deepseek-flash 的缓存命中输入单价只有未命中输入的 1/50, 混在一个总数里看不出区别.

五列**全部可空**, 与 `total_tokens` 的 `NOT NULL DEFAULT 0` 不同, 这不是手滑:
`NULL` 说的是「上游一次都没上报过这个分量」(例如流式响应没带 usage), 而 `0` 是
「报过了, 值就是零」. 在成本归因里这两句话的结论相反 (真的一次没命中缓存 / 这次
压根没拿到缓存数据), 所以不能让缺省值把它们混掉. 本迁移之前写下的行一律是 NULL.

没有 `prompt_version` 什么事: 那一列在 0001 里就建好了, 只是此前没人往里写 (§40
版本化留给 L3), 填值不需要改结构.

写法与 0001 一致 (显式 `op.add_column` + `comment=`, 不 import 代码里的 metadata):
**迁移脚本是历史, 必须冻住**. 本文件是手写的, 随后由 `pytest -m pg_db` 里的
`compare_metadata` 逐列比对确认它与 `db/schema.py` 没有漂移 —— 那条用例才是这道
纪律的真正保险, 而不是 `--autogenerate` 这个动作.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_run_usage_breakdown"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 这一版加的五列 (列名 + 库注释), 注释与 db/schema.py 逐字一致 —— 两边不一致时
# --autogenerate 每次都会报「注释变了」这种与真实改动无关的差异.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("input_tokens", "本 run 累计输入 token (prompt_tokens); NULL = 上游一次都没上报"),
    ("output_tokens", "本 run 累计输出 token (completion_tokens, 含思维链)"),
    (
        "reasoning_tokens",
        "本 run 累计思维链 token (completion_tokens_details.reasoning_tokens)",
    ),
    ("cache_hit_tokens", "本 run 累计命中上下文缓存的输入 token (单价远低于未命中)"),
    ("cache_miss_tokens", "本 run 累计未命中缓存的输入 token"),
)


def upgrade() -> None:
    """给 charagent_runs 加五列 (全部可空: NULL = 上游没上报过这个分量).

    `server_default` 一律不给: 老行该是 NULL (那时候还没有这个字段, 而不是「那时候
    用量为零」), 而新行由写入侧显式给值 —— 有默认值反而会让「忘了传」看起来像
    「确实是零」.
    """
    for name, comment in _COLUMNS:
        op.add_column(
            "charagent_runs",
            sa.Column(name, sa.BigInteger(), nullable=True, comment=comment),
        )


def downgrade() -> None:
    """删掉这五列 (分解数据一并没了; 总量 total_tokens 不受影响).

    只有「确定不再需要归因」时才该走这条 —— 它是纯减法, 撤不回来.
    """
    for name, _ in reversed(_COLUMNS):
        op.drop_column("charagent_runs", name)
