"""五实体核心表 + 版本表的审计两列: 压缩后的唯一起点

Revision ID: 0001_core
Revises:
Create Date: 2026-09-23

一句话说明: 数据层的**唯一起点** —— 建五张业务表, 再给 alembic 的版本表补两列
审计信息 (`name` / `history`), 让「这张库现在哪一版、这一版何时由谁上的、一路
是怎么走过来的」在一张表里就答得完.

**这是一次压缩** (2026-09-23, ticket 24): 原来的 `0001_core` /
`0002_run_usage_breakdown` / `0003_migration_audit_log` /
`0004_frame_run_linkage` 被压成这一条. 理由是**还没有发布**: 只有本机这一个库
用过那四个编号, 压掉不欠谁的; 而一份「一步到位」的初始迁移, 比四条「历史层层
叠加」好读得多. 压缩后本文件就是新的起点, 「迁移脚本是历史, 必须冻住」这条规矩
从这里重新算起 —— 见 `db/README.md`「改 schema 的流程」.

与压缩前那条 head 的差异**只有三处** (其余列 / 类型 / 可空 / 默认 / 索引 /
外键名 / 注释逐字相同):

1. `charagent_checkpoints.thread_id` 补上指向 `charagent_threads` 的外键
   (CASCADE) —— 原先只有注释说它是「分区键」, 却没有约束保证那个会话真的存在.
   连带一条行为契约: **写帧之前, 会话行必须已经存在** (帧不再能先于会话行).
2. `charagent_migrations` 从「累积审计表」变成 **alembic 的版本表**: 表由 alembic
   在跑迁移之前自己建好 (只带 `version_num` 一列, 主键名也由它定), 本迁移只往后
   补 `name` 与 `history` 两列. 于是「现在是哪一版」与「上过哪几版、每一版各是什么
   时候由谁成为当前版的」落在同一张表里.
3. 两张表的**物理列序**回到 `db/schema.py` 的声明序: `charagent_runs` (五个用量列
   与 `last_checkpoint_id` 原先被 `ALTER ADD COLUMN` 追加在表尾) 与
   `charagent_checkpoints` (`run_id` 是 0004 追加的, 也在表尾).

为什么用 `op.create_table` 显式写出每张表 (而不是 import 代码里的 metadata 去建):
**迁移脚本是历史, 必须冻住** —— 若从 db/schema.py 动态取表定义, 以后改字段会让
这份「第一次迁移」跟着变, 于是「在老库上跑这条迁移」与「在新库上跑同一条迁移」
建出不同的表, 版本号就失去意义了. 表定义以后怎么改由**后续迁移**表达.

列注释与表注释都走 `comment=` 参数 (alembic 会翻译成 `COMMENT ON`), 与
db/schema.py 逐字一致 —— 两边不一致时 `--autogenerate` 每次都会报「注释变了」
这种与真实改动无关的差异, 那些噪音会淹掉真正的 schema 变更.

两处**不能照抄 db/schema.py 的写法** (那边是声明式, 这边是执行序):

- `charagent_runs.last_checkpoint_id` 与 `charagent_checkpoints.run_id` 互为外键
  (两张表之间的环). `checkpoints` 建表时 `runs` 已经在了, 那条外键内联即可;
  而 `runs` 建表时 `checkpoints` 还不存在, 所以 `runs.last_checkpoint_id` 的外键
  **留到两张表都建完再 ALTER** —— 这正是 db/schema.py 给它标 `use_alter=True`
  的原因 (标了才能建出来, 而不是只报一句警告然后悄悄少一个外键).
- `charagent_migrations` 不在这里 `create_table`: 它由 alembic 建, 见上面第 2 条.

**对「库里已有旧表」的库**: 本迁移会在 `create_table` 上报「关系已存在」而失败
—— 这是有意的 (建表语句就是它该建的东西, 不该悄悄跳过). 处理办法见
`db/README.md`「改 schema 的流程」: 旧表为空就直接删掉重跑, 有数据则先备份
再重建.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charagent_threads",
        sa.Column(
            "thread_id",
            sa.String(length=128),
            nullable=False,
            comment="会话编号 (uuid4 hex)",
        ),
        sa.Column(
            "tenant_id",
            sa.String(length=128),
            nullable=False,
            comment="租户编号 —— 多租户隔离的过滤键 (#32), P1 起所有查询强制带上",
        ),
        sa.Column(
            "user_id",
            sa.String(length=128),
            nullable=False,
            comment="会话属主 —— 决定「谁能看到这段对话」",
        ),
        sa.Column(
            "title",
            sa.String(length=255),
            server_default="",
            nullable=False,
            comment="会话标题 (由首条用户消息生成, 前端列表用)",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
            comment="会话状态 (ThreadStatus: active / closed / escalated)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时刻",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="最后活动时刻 —— 会话列表按它排序 (新的在前)",
        ),
        sa.PrimaryKeyConstraint("thread_id", name="pk_charagent_threads"),
        comment="会话: 一段对话的容器, 也是数据隔离的单位 (所有东西都挂在它下面)",
    )
    op.create_index(
        "ix_charagent_threads_tenant_updated",
        "charagent_threads",
        ["tenant_id", "updated_at"],
    )
    op.create_index(
        "ix_charagent_threads_user_updated",
        "charagent_threads",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "charagent_runs",
        sa.Column(
            "run_id",
            sa.String(length=128),
            nullable=False,
            comment="本次执行的编号",
        ),
        sa.Column(
            "thread_id",
            sa.String(length=128),
            nullable=False,
            comment="所属会话; 会话删掉, 它的历次执行一并删掉 (CASCADE)",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="created",
            nullable=False,
            comment=(
                "运行状态机 (RunStatus): created / running / waiting_tool / "
                "waiting_user / retrying / failed / finished / cancelled"
            ),
        ),
        sa.Column(
            "request_id",
            sa.String(length=255),
            nullable=True,
            comment="幂等键 (#17): 同一个 request_id 重复提交直接返回已有 run, 不重跑",
        ),
        sa.Column(
            "model",
            sa.String(length=128),
            nullable=True,
            comment="本次 run 用的模型名 —— 版本化 (#40) 的基础",
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=64),
            nullable=True,
            comment="本次 run 用的 prompt 版本 (#40)",
        ),
        sa.Column(
            "total_tokens",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
            comment="本 run 累计 token (#34 成本归因按 task)",
        ),
        # 用量分解 (#34): total_tokens 是账单原值, 下面五列是它的归因拆解 ——
        # 「钱花在哪一类 token 上」靠它们才答得出来 (deepseek-flash 的缓存命中输入
        # 单价只有未命中的 1/50, 两者混在一个总数里看不出区别). 五列**都可空**:
        # NULL 说的是「上游一次都没上报过这个分量」(如流式未带 usage), 与 0
        # (报过、值就是零) 是两回事 —— 成本归因里这两个的结论相反.
        sa.Column(
            "input_tokens",
            sa.BigInteger(),
            nullable=True,
            comment="本 run 累计输入 token (prompt_tokens); NULL = 上游一次都没上报",
        ),
        sa.Column(
            "output_tokens",
            sa.BigInteger(),
            nullable=True,
            comment="本 run 累计输出 token (completion_tokens, 含思维链)",
        ),
        sa.Column(
            "reasoning_tokens",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "本 run 累计思维链 token (completion_tokens_details.reasoning_tokens)"
            ),
        ),
        sa.Column(
            "cache_hit_tokens",
            sa.BigInteger(),
            nullable=True,
            comment="本 run 累计命中上下文缓存的输入 token (单价远低于未命中)",
        ),
        sa.Column(
            "cache_miss_tokens",
            sa.BigInteger(),
            nullable=True,
            comment="本 run 累计未命中缓存的输入 token",
        ),
        sa.Column(
            "total_cost",
            sa.Numeric(precision=14, scale=6),
            server_default="0",
            nullable=False,
            comment="本 run 累计花费 (金额用 NUMERIC 不用浮点: 浮点算钱会丢分)",
        ),
        sa.Column(
            "turn_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="已执行的 Turn 数 (断点续跑时是累计值, 不从头数)",
        ),
        sa.Column(
            "error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="失败原因 (结构化: code + message), 供审计与降级判断",
        ),
        sa.Column(
            "last_checkpoint_id",
            sa.String(length=128),
            nullable=True,
            comment="这一段运行落的**最后一帧** (快照); 顺它的 parent_id 往回走就是"
            "本次运行落的全部帧 —— NULL = 没配快照存储 (或一帧都没落成)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时刻",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="最后更新时刻",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="结束时刻; NULL 表示还在跑 (或没跑到终点就丢了)",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["charagent_threads.thread_id"],
            name="fk_charagent_runs_thread_id_charagent_threads",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_charagent_runs"),
        sa.UniqueConstraint("request_id", name="uq_charagent_runs_request_id"),
        comment="一次执行: 用户点一次「发送」到 agent 答完, 有自己的状态机与账目",
    )
    op.create_index(
        "ix_charagent_runs_thread_created",
        "charagent_runs",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "charagent_messages",
        sa.Column(
            "message_id",
            sa.String(length=128),
            nullable=False,
            comment="消息编号",
        ),
        sa.Column(
            "thread_id",
            sa.String(length=128),
            nullable=False,
            comment="所属会话",
        ),
        sa.Column(
            "run_id",
            sa.String(length=128),
            nullable=True,
            comment="归属哪次执行; NULL = 不是 agent 跑出来的 "
            "(比如人工客服接管后的回复)",
        ),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            comment="发言者 (MessageRole): user / assistant / tool / system",
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=True,
            comment="文本内容 —— assistant 的最终答复就在这里",
        ),
        sa.Column(
            "reasoning",
            sa.Text(),
            nullable=True,
            comment=(
                "assistant 的思维链 (reasoning_content): 存下来供前端折叠展示与审计. "
                "注意这与 wire 历史的回填是两码事 —— 前者是「给人看」, 后者是「喂模型」"
            ),
        ),
        sa.Column(
            "tool_call_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
            comment="这条 assistant 消息发起了哪些工具调用 (列表, 保持并行语义 #1)",
        ),
        sa.Column(
            "hidden",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="内部消息: true = 不展示给前端 (续写指令 / 压缩摘要 / 工具回填 / "
            "带工具调用的中间轮). 会话历史接口只取 false 的那些 "
            "(见 conversation.py)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="产生的时刻 (会话历史按它排序)",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["charagent_threads.thread_id"],
            name="fk_charagent_messages_thread_id_charagent_threads",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["charagent_runs.run_id"],
            name="fk_charagent_messages_run_id_charagent_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("message_id", name="pk_charagent_messages"),
        comment="消息: 一问一答里的那一条 (hidden=False 的才给前端看)",
    )
    op.create_index(
        "ix_charagent_messages_thread_created",
        "charagent_messages",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "charagent_tool_calls",
        sa.Column(
            "run_id",
            sa.String(length=128),
            nullable=False,
            comment="归属哪次执行 (复合主键的三分之一)",
        ),
        sa.Column(
            "message_id",
            sa.String(length=128),
            nullable=False,
            comment="是哪条 assistant 消息发起的 (复合主键的三分之一, 且**不能为空**)",
        ),
        sa.Column(
            "tool_call_id",
            sa.String(length=128),
            nullable=False,
            comment="模型给的调用编号 (复合主键的三分之一). **为什么主键要三列**: "
            "真实上游每轮都从 call_0 重新编号 —— 同一个 run 里会出现好几条"
            "「call_0」, 单列主键第二次就撞; 而加上 run_id 只解决了「跨 run」, "
            "同一个 run 的多轮之间仍会撞. 真正唯一的身份是「哪条 assistant 消息"
            "发起的这一次调用」, 所以 (run_id, message_id, tool_call_id) 三列才是"
            "完整的键",
        ),
        sa.Column(
            "tool_name",
            sa.String(length=128),
            nullable=False,
            comment="调用的工具名",
        ),
        sa.Column(
            "arguments",
            sa.Text(),
            server_default="",
            nullable=False,
            comment="模型填的参数, **原样 JSON 字符串** (不预解析): 畸形 JSON 正是 "
            "自纠错路径的信号 (#2), 解析了反而丢证据. 想查字段用 arguments::jsonb",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
            comment="执行状态 (ToolCallStatus): pending / running / succeeded / failed "
            "/ cancelled / needs_approval (HITL 挂起等人工批准 #25)",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="执行结果 (成功) 或可操作错误信息 (#2: 说清字段格式问题, 不甩 422)",
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=True,
            comment="耗时毫秒; NULL = 没跑到计时那一步",
        ),
        sa.Column(
            "approved_by",
            sa.String(length=128),
            nullable=True,
            comment="HITL 审批人 (#25); NULL = 无需审批或还没批",
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="HITL 审批时刻 (#25)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="发起时刻",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="状态最后变化时刻 —— 追问「这条调用卡了多久」看它",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["charagent_runs.run_id"],
            name="fk_charagent_tool_calls_run_id_charagent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["charagent_messages.message_id"],
            name="fk_charagent_tool_calls_message_id_charagent_messages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "message_id", "tool_call_id", name="pk_charagent_tool_calls"
        ),
        comment="工具调用: 模型「我要去查一下」的那一下 —— 参数 / 状态 / 结果 / 审批",
    )
    op.create_index(
        "ix_charagent_tool_calls_run_created",
        "charagent_tool_calls",
        ["run_id", "created_at"],
    )

    op.create_table(
        "charagent_checkpoints",
        sa.Column(
            "checkpoint_id",
            sa.String(length=128),
            nullable=False,
            comment="快照编号",
        ),
        sa.Column(
            "thread_id",
            sa.String(length=128),
            nullable=False,
            comment="所属会话 (分区键: 一个会话的所有快照排成一条线); "
            "会话删掉, 它的快照一并删掉 (CASCADE)",
        ),
        sa.Column(
            "run_id",
            sa.String(length=128),
            nullable=True,
            comment="归属记录层的哪一行账 (charagent_runs); NULL = 不属于任何一行 "
            "(没配记录层的进程 / 那一轮没记上账 / 老帧)",
        ),
        sa.Column(
            "loop_id",
            sa.String(length=128),
            nullable=False,
            comment="哪一次循环执行存下的 (一次循环执行的几帧共享; 续跑沿用)",
        ),
        sa.Column(
            "turn_number",
            sa.Integer(),
            nullable=False,
            comment="存下它时已经跑完几轮 (「执行到哪一步」#5)",
        ),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            comment="快照内容的格式版本号 (#5 向前兼容): 老快照读回来要按它升级",
        ),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="进度: 消息历史 + 计数器 + 挂起点 (恢复才用, "
            "序列化协议见 checkpoint/serialization.py)",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
            comment="观察值: 来源 + 本轮 token 与耗时 + 工具 + 结束原因 "
            "(给人看, 回放调试用; v3 起从 state 里分出来)",
        ),
        sa.Column(
            "parent_id",
            sa.String(length=128),
            nullable=True,
            comment="上一帧的编号 —— 从头排到尾是一条链; 从老快照恢复则在那里"
            "岔出新分支 (time-travel #5). NULL = 这条线的头一份",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="存下的时刻 (带时区; 排序与回溯都看它)",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["charagent_threads.thread_id"],
            name="fk_charagent_checkpoints_thread_id_charagent_threads",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["charagent_runs.run_id"],
            name="fk_charagent_checkpoints_run_id_charagent_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["charagent_checkpoints.checkpoint_id"],
            name="fk_charagent_checkpoints_parent_id_charagent_checkpoints",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("checkpoint_id", name="pk_charagent_checkpoints"),
        comment="会话快照: 一帧一行, 全历史都在 (agent/loop.py 每 Turn 末尾落一帧)",
    )
    op.create_index(
        "ix_charagent_checkpoints_thread_created",
        "charagent_checkpoints",
        ["thread_id", "created_at", "checkpoint_id"],
    )

    # 环的那一半: `runs` 建表时 `checkpoints` 还不存在, 推迟到这里补.
    op.create_foreign_key(
        "fk_charagent_runs_last_checkpoint_id_charagent_checkpoints",
        "charagent_runs",
        "charagent_checkpoints",
        ["last_checkpoint_id"],
        ["checkpoint_id"],
        ondelete="SET NULL",
    )

    # --- 版本表的审计两列 -------------------------------------------------------
    #
    # `charagent_migrations` 由 alembic 在跑本迁移**之前**建好 (它拿这张表当版本表,
    # 表名来自 alembic/env.py 的 VERSION_TABLE), 建出来的形状是 `version_num` 一列
    # 加一个由它命名的主键 (`charagent_migrations_pkc`) —— 所以这里**不 create_table**
    # (会撞「关系已存在」), 只往后补我们自己的两列.
    #
    # 两列都必须扛得住「alembic 只写 version_num」的那条 INSERT: `name` 给
    # server_default=""、`history` 给 server_default="{}"; 真正的值由
    # alembic/env.py 的钩子在**同一步**里回填 (它拿这一版是从哪一版上来的、执行者
    # 与库的时钟拼一条, 记进 history 里这一版那个键).
    op.add_column(
        "charagent_migrations",
        sa.Column(
            "name",
            sa.String(length=200),
            server_default="",
            nullable=False,
            comment="当前这一版的标题 (脚本 docstring 的首行, 与 `alembic history` "
            "印的是同一句); 空串 = 钩子还没回填",
        ),
    )
    op.add_column(
        "charagent_migrations",
        sa.Column(
            "history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
            comment="迁移史 (dict: 键 = 迁移编号, 值 = 这一版最近一次成为当前版时的 "
            "{from, at, by}; from = null 表示从空库起). 回退会改写目标版那一条, "
            "于是「首次上线时刻」不留 —— 要留档先导出",
        ),
    )
    op.create_table_comment(
        "charagent_migrations",
        "这张库的迁移状态: 唯一一行 = 当前版本. `version_num` 由 alembic 自己写 "
        "(它拿这一列当 head), `name` / `history` 由 alembic/env.py 的钩子回填",
    )


def downgrade() -> None:
    """回退: 先拆掉环, 再按依赖倒序删表 (被引用的后删).

    顺序不能乱: `charagent_runs.last_checkpoint_id` 指着 `charagent_checkpoints`,
    所以那条外键必须先拆 —— 否则删 `checkpoints` 会报「被别的表引用」.
    `charagent_checkpoints` 的 `parent_id` 是自引用外键, 自引用的表可以自己先删
    (同一个 DROP 里外键与表一起走).

    版本表的两列也拆掉, 让它回到 alembic 建出来的裸形状: `downgrade base` 的语义
    是「这张库回到未初始化」, 留一列空的历史反而是撒谎. **注意 alembic 随后会
    删掉唯一那行 —— 连同 history 一起没** (它在线模式从不删版本表本身).

    要保住快照数据就别 downgrade.
    """
    # 表注释一并撤掉 (`COMMENT ON TABLE ... IS NULL`). 不给 `existing_comment`:
    # 那个参数只在 autogenerate 比差异时用, 而这张表是 alembic 的版本表、两边的
    # 比对都把它排除在外 —— 传它等于把同一句注释再抄一遍 (抄本会漂移).
    op.drop_table_comment("charagent_migrations")
    op.drop_column("charagent_migrations", "history")
    op.drop_column("charagent_migrations", "name")

    op.drop_constraint(
        "fk_charagent_runs_last_checkpoint_id_charagent_checkpoints",
        "charagent_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_charagent_checkpoints_thread_created")
    op.drop_table("charagent_checkpoints")
    op.drop_index("ix_charagent_tool_calls_run_created")
    op.drop_table("charagent_tool_calls")
    op.drop_index("ix_charagent_messages_thread_created")
    op.drop_table("charagent_messages")
    op.drop_index("ix_charagent_runs_thread_created")
    op.drop_table("charagent_runs")
    op.drop_index("ix_charagent_threads_user_updated")
    op.drop_index("ix_charagent_threads_tenant_updated")
    op.drop_table("charagent_threads")
