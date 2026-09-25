"""幂等登记表: 一条键一行, 挡住「同一个动作被执行两遍」

Revision ID: 0004_idempotency_keys
Revises: 0003_run_cost_columns
Create Date: 2026-09-25

一句话说明: 新建 `charagent_idempotency_keys` —— 幂等键的**持久化**登记簿
(ticket 32, 依据 ADR-0017).

**这张表解决什么**: 重试让「同一件事」可能被执行两次, 而查询两次没关系、退款
两次就是事故. `retry/idempotency.py` 早就有键与协议 (claim / complete / release),
但它的实现是**进程内 dict** —— HITL 的挂起-恢复跨进程 (两次 HTTP 请求可能落同一个
进程, 也可能不是; 用户关掉浏览器隔天再点也不是没有可能), 内存实现挡不住这类重放.
本表就是那个能跨进程的登记簿.

**规模**: 本迁移只加**一张表** (`db/README.md` 的 P1 段早就规划了「P1 只加这一张」),
不碰任何既有表 —— 于是老数据零改写, 也不需要在迁移里回填什么.

**为什么不做后台清理**: 列 `expires_at` 有, 但没人定时扫它. 清理是运维动作, 与
「能挡住重放」无关, 而一个常驻清理任务要多一处收尾、多一个测试面, 换来的只是
「表小一点」. 真要清理时, 加一条 `DELETE FROM ... WHERE expires_at < now()` 的定时
任务即可 —— **表结构不用改** (这一条是刻意的边界, 不是漏了一半).

**过期之后能不能重新认领**: 能 (见 `expires_at` 那一列的注释). 它已经不是同一次
操作了 —— 于是「过期记录」与「从没见过的键」对调用方是同一种东西. 这项语义写进了
列注释 (库里能看到), 也有一条用例钉住.

**为什么这一列的键是主键**: 键就是这张表的全部身份. 而认领的原子性正好落在主键上:
`ON CONFLICT (key) DO NOTHING / DO UPDATE` 是「谁先插进去谁拿到执行权」在数据库层
的写法 —— 比应用层「先查后插」多一道真正不会漏的闸 (与 `charagent_runs.request_id`
那条唯一约束同一条理由, 那条注释在 `db/schema.py` 里).

列注释与表注释都走 `comment=` 参数 (alembic 会翻译成 `COMMENT ON`), 与
`db/schema.py` 逐字一致 —— 两边不一致时 `--autogenerate` 每次都会报「注释变了」,
那些噪音会淹掉真正的 schema 变更.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_idempotency_keys"
down_revision: str | None = "0003_run_cost_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charagent_idempotency_keys",
        sa.Column(
            "key",
            sa.String(length=255),
            nullable=False,
            comment="幂等键本身 —— 长度上限 255 与 retry/idempotency.py 的 "
            "MAX_KEY_LENGTH 同源 (那个模块构造时就挡掉超长与白名单外的字符, 于是这一列"
            "只会收到合法值)",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            comment="认领状态 (ClaimStatus): in_progress = 有人在做 "
            "(重复请求会被挡回), completed = 已做完且结果在 result 列. 三态里"
            "的 claimed 是**本次认领成功的答复**, 不是存下来的状态 —— 谁拿到了"
            "执行权, 写下来的都是 in_progress",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="完成时的结果 (complete(key, result) 存下的东西, 可 JSON 序列化); "
            "NULL = 还没做完 (在途)",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="过期时刻 (NULL = 永不过期, 本框架的默认). **过了这一刻同一个"
            "键可以重新认领** —— 那时它已经不是同一次操作了, 与「永不过期」是两种"
            "口径, 这里取前者. 它只在**认领**那一刻定下, 之后完成与释放都不改它"
            " (唯一例外是没有认领记录的补登, 那时按当下的配置现算一次)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="本次认领时刻 (过期被重新认领时随之改写 —— 那一行从此描述的是新的"
            "那一次操作)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="状态最后变化时刻 (认出「这条卡了多久」看它)",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_charagent_idempotency_keys"),
        comment="幂等登记簿: 一条键一行 —— 认领过 / 正在做 / 做完了结果是啥 (#17 的"
        "持久化底座, 调用方是 HITL 的挂起-恢复)",
    )


def downgrade() -> None:
    # 退回去就是把登记簿整张丢掉 (它没有外键, 也没有任何表引用它 —— 删它不影响
    # 别人). 代价是老代码若已经在用这把持久化存储, 回退后会退回进程内实现
    # (`retry/idempotency.py` 的 InMemoryIdempotencyStore), 即跨进程重放重新变成
    # 敞口 —— 这正是本条迁移存在的原因, 回退前要清楚这一点
    op.drop_table("charagent_idempotency_keys")
