"""逐模型用量列: 一趟里换过家时, 金额才拆得开

Revision ID: 0006_run_usage_by_model
Revises: 0005_tool_call_approval_columns
Create Date: 2026-10-05

一句话说明: 给 `charagent_runs` 加一列 `usage_by_model` —— **哪一家模型产出了多少
token**, 按模型名去重; 每一段运行收尾时把自己那一段并进来.

**为什么需要它** (difficulties #14, 见 `CharApp/docs/adr/0025`): 熔断切到备份之后,
一趟运行可能被两家服务过. 运行行那五列是**累计值**(计数器跨快照接着数), 拿它乘任何
一家的单价都是错数 —— 要算对, 得知道「谁产出了多少」。段内那条路靠逐轮响应上的
服务方名字 (`LoopResult.turns`); 而 HITL 续跑的第二段手里**只有本段的轮**, 上一段的
那份不再存在 —— 唯一能补救它的是**上一段收尾的那一刻**, 也就是这一列.

**它是累积写还是覆盖写**: 覆盖写, 写的是**合并后的完整快照**(列上那份 + 本段那份按
模型名相加). 记录员只在归并结果与运行行那几列**对得上**时才写 —— 写一半进去会让下一
段在脏数据上继续合, 不如不写 (读不到就退回「按一个名字算」那条路, 金额留空).

**可空**: NULL = 没有可归因的逐模型用量 (老行 / 一段里一次模型调用都没有 / 归并不齐).
与「空列表」不是一回事 —— 后者是「确认过: 一家都没产出」.

**JSON 的形状** (键名与 `db/cost.py` 的 `ModelUsage` 字段同名, 缺的分量**不写键**):

```json
[{"model": "deepseek-flash", "input_tokens": 1000, "cache_miss_tokens": 1000,
  "output_tokens": 100}, {"model": "gpt-6-luna", ...}]
```
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_run_usage_by_model"
down_revision: str | None = "0005_tool_call_approval_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 db/schema.py 的 `_USAGE_BY_MODEL_COMMENT` 逐字一致 (改一处必须改两处)
_COMMENT = (
    "逐模型用量 (difficulties #14 的拆账依据): 形如 "
    "[{model, input_tokens, cache_miss_tokens, cache_hit_tokens, output_tokens}], "
    "按模型名去重、按首次出场排序; 每一段收尾时把自己那一段并进来 (同名的相加). "
    "NULL = 没有可归因的逐模型用量 (老行 / 一段里一次模型调用都没有 / 归并不齐)"
)


def upgrade() -> None:
    op.add_column(
        "charagent_runs",
        sa.Column("usage_by_model", JSONB(), nullable=True, comment=_COMMENT),
    )


def downgrade() -> None:
    op.drop_column("charagent_runs", "usage_by_model")
