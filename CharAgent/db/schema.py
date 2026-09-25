"""表定义的**唯一定义处**: 表长什么样只在这一个文件里说.

一句话理解: 这个文件是**数据库的户型图**. 六张业务表 (会话 / 运行 / 消息 / 工具
调用 / 快照 / 幂等登记) 各有哪些列、哪列是主键、谁引用谁、建哪些索引, 全部写在这里.
别处 (仓储 / 快照存储 / alembic 迁移 / 测试) 都从这里取, 不许自己再抄一份 ——
抄两份的结果一定是「改了一份忘了另一份」, 然后代码与库悄悄对不上.

**`charagent_migrations` (alembic 的版本表) 例外, 不在这里声明** (ticket 24): 它是
工具的表 —— 列名 `version_num` 与主键名 `charagent_migrations_pkc` 都由 alembic
自己定, 我们跟着声明一份只会与它打架. 而 `alembic check` 会按 `version_table`
把这张表从**代码侧与库侧两边**都排除, 所以不声明它, 零差异照样成立. 它的形状、
后补的那两列审计信息 (`name` / `history`) 与怎么回填, 见
`alembic/versions/0001_core.py` 与 `alembic/env.py`.

对齐 entities.py 的实体字段表 —— 那个表是**业务视角**(这一列是干什么用的),
本文件是**存储视角**(这一列在库里是什么类型、能不能为空). 两边应当是一一对应的,
有出入就是有一处过时了 (**`charagent_idempotency_keys` 是例外, 它没有实体**:
这张表没有「业务视角」的那一半, 只有「认领」一个动作, 形状由 `retry/` 的协议
说了算 —— 理由见那张表的注释).

**表名为什么一律带 `charagent_` 前缀** (而不是就叫 threads / runs / messages):
本项目各子项目共用同一个 Postgres 库 (根 .env 的 PGSQL_*). `threads` / `runs` /
`messages` 是极常见的表名, 别的子项目随时可能占掉; 而 `checkpoints` 已经真撞过
一次 —— `langgraph-checkpoint-postgres` 也建一张同名表, 它的
`CREATE TABLE IF NOT EXISTS checkpoints` 会因为「表已存在」被静默跳过, 随后
INSERT 报「列不存在」, 报错信息跟真正的原因 (表名撞了) 毫无关系, 极难排查
(2026-09-13 实测). 自己的表带自己的前缀, 这类撞名从源头消失.

**时间列为什么一律 TIMESTAMPTZ** (而不是无时区的 TIMESTAMP): 这些表的排序键
全是时间 (快照的「取最新一帧」、消息的「按时间翻历史」), 而无时区的时间戳
读回来是「哪儿的几点」都不知道的裸数字 —— 换台机器、跨个时区, 排序就可能悄悄
错位. 带时区存, 读回来永远是绝对时刻.

**JSONB 列装什么**: 形状会随版本变、或者本来就不规则的数据 (工具参数 / 工具
结果 / 失败原因 / 快照的进度与观察值). 用 JSONB 而不是拆成一堆列, 是为了
「加个字段不必改表」; 而身份与查询条件 (编号 / 状态 / 时间) 一律各自成列,
因为它们要建索引、要按条件查.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

# 约束的命名规则: 数据库自动起的名字是随机串, 迁移脚本里想改一个约束就得先去
# 库里查它叫什么. 定死规则后, 名字可以算出来 (如 uq_charagent_runs_request_id),
# 迁移脚本与排查都能直接引用.
#
# 规则映射 (SQLAlchemy 的约定键 → 生成的名字):
#   ix  -> ix_<表>_<列>      索引
#   uq  -> uq_<表>_<列>      唯一约束
#   ck  -> ck_<表>_<列>      CHECK 约束
#   fk  -> fk_<表>_<列>_<被引用的表>
#   pk  -> pk_<表>           主键
# "%(column_0_N_name)s" 这种占位符只取**参与该约束的那些列**的拼接名, 于是复合
# 索引会生成 `ix_charagent_tool_calls_run_id_tool_call_id` 这样的可读名字.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# 全库共用的元数据容器: 六张表都挂在它下面. alembic 的 autogenerate 拿它当
# 「代码侧应该长什么样」的基准, alembic/env.py 的 target_metadata 就是它.
metadata = MetaData(naming_convention=NAMING_CONVENTION)

# 枚举取值的长度上限: 状态名最长的是 insufficient_system_resource 那类, 但下面
# 这些枚举里最长的是 "needs_approval" (14) —— 留一倍余量, 既不会写不下, 也不会
# 因为某个状态改名就要改表结构.
_ENUM_LEN = 32

# --- 六张业务表 ---------------------------------------------------------------

threads = Table(
    "charagent_threads",
    metadata,
    Column("thread_id", String(128), primary_key=True, comment="会话编号 (uuid4 hex)"),
    Column(
        "tenant_id",
        String(128),
        nullable=False,
        comment="租户编号 —— 多租户隔离的过滤键 (#32), P1 起所有查询强制带上",
    ),
    Column(
        "user_id",
        String(128),
        nullable=False,
        comment="会话属主 —— 决定「谁能看到这段对话」",
    ),
    Column(
        "title",
        String(255),
        nullable=False,
        server_default="",
        comment="会话标题 (由首条用户消息生成, 前端列表用)",
    ),
    Column(
        "status",
        String(_ENUM_LEN),
        nullable=False,
        server_default="active",
        comment="会话状态 (ThreadStatus: active / closed / escalated)",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时刻",
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最后活动时刻 —— 会话列表按它排序 (新的在前)",
    ),
    # 下面两列是用户对会话做的两个管理动作 (#20). 都取**时刻**而不是布尔:
    # 「置不置顶」布尔够用, 而「谁先置顶的」只有时刻说得清 —— 既然要加列, 就加
    # 撑得住排序语义的那一个; `deleted_at` 同理, 一列同时回答「删了没」与「何时删的」.
    Column(
        "pinned_at",
        DateTime(timezone=True),
        nullable=True,
        comment="置顶时刻 —— NULL = 未置顶; 有值 = 置顶那一刻 (列表把置顶项排最前)",
    ),
    Column(
        "deleted_at",
        DateTime(timezone=True),
        nullable=True,
        comment="删除时刻 —— NULL = 还在; 有值 = 已被用户删除 (软删: 行与消息都留着)",
    ),
    # 会话列表的两种查法: 「这个租户的会话」(管理端) / 「这个用户的会话」(用户端).
    # 两者都要按最后活动时间倒序, 所以索引里带上 updated_at.
    Index("ix_charagent_threads_tenant_updated", "tenant_id", "updated_at"),
    Index("ix_charagent_threads_user_updated", "user_id", "updated_at"),
    # 表级注释 (会变成库里的 COMMENT ON TABLE)
    comment="会话: 一段对话的容器, 也是数据隔离的单位 (所有东西都挂在它下面)",
)

runs = Table(
    "charagent_runs",
    metadata,
    Column("run_id", String(128), primary_key=True, comment="本次执行的编号"),
    Column(
        "thread_id",
        String(128),
        ForeignKey("charagent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话; 会话删掉, 它的历次执行一并删掉 (CASCADE)",
    ),
    Column(
        "status",
        String(_ENUM_LEN),
        nullable=False,
        server_default="created",
        comment="运行状态机 (RunStatus): created / running / waiting_tool / "
        "waiting_user / retrying / failed / finished / cancelled",
    ),
    Column(
        "request_id",
        String(255),
        nullable=True,
        comment="幂等键 (#17): 同一个 request_id 重复提交直接返回已有 run, 不重跑",
    ),
    Column(
        "model",
        String(128),
        nullable=True,
        comment="本次 run 用的模型名 —— 版本化 (#40) 的基础",
    ),
    Column(
        "prompt_version",
        String(64),
        nullable=True,
        comment="本次 run 用的 prompt 版本 (#40)",
    ),
    Column(
        "total_tokens",
        BigInteger,
        nullable=False,
        server_default="0",
        comment="本 run 累计 token (#34 成本归因按 task)",
    ),
    # 用量分解 (#34): total_tokens 是账单原值, 下面五列是它的归因拆解 —— 「钱花在
    # 哪一类 token 上」靠它们才答得出来 (deepseek-flash 的缓存命中输入单价只有未
    # 命中的 1/50, 两者混在一个总数里看不出区别). 五列**都可空**: NULL 说的是
    # 「上游一次都没上报过这个分量」(如流式未带 usage), 与 0 (报过、值就是零) 是
    # 两回事 —— 成本归因里这两个的结论相反.
    Column(
        "input_tokens",
        BigInteger,
        nullable=True,
        comment="本 run 累计输入 token (prompt_tokens); NULL = 上游一次都没上报",
    ),
    Column(
        "output_tokens",
        BigInteger,
        nullable=True,
        comment="本 run 累计输出 token (completion_tokens, 含思维链)",
    ),
    Column(
        "reasoning_tokens",
        BigInteger,
        nullable=True,
        comment="本 run 累计思维链 token (completion_tokens_details.reasoning_tokens)",
    ),
    Column(
        "cache_hit_tokens",
        BigInteger,
        nullable=True,
        comment="本 run 累计命中上下文缓存的输入 token (单价远低于未命中)",
    ),
    Column(
        "cache_miss_tokens",
        BigInteger,
        nullable=True,
        comment="本 run 累计未命中缓存的输入 token",
    ),
    # 金额在**收尾那一刻**算好写死 (2026-09-25 改判, 理由见 db/cost.py 模块 docstring):
    # 同一批 token 换一版价目表就是另一个数, 而账单是按**当时那一版**开的 —— 查询侧
    # 现算会让历史运行的金额跟着今天的价变, 与账单永远对不上. 峰谷价更逼着把「算的
    # 那一刻」固定下来 (判据是本次运行的开始时刻, 只有 run 行知道).
    #
    # **可空**: NULL = 那一刻没算出来 (没配价 / 缺分量 / 日历过期), 0 = 真的花了 0 元
    # —— 与五列分量同一条规矩, 「没算出来」与「就是零」是相反的结论. 只有算得出来时
    # 才写, 写进去之后任何一次写都不再改它 (金额与账单绑定).
    Column(
        "total_cost",
        Numeric(14, 6),
        nullable=True,
        comment="本次运行的花费 —— 收尾那一刻按当时的价目表算好写入 "
        "(NULL = 没算出来, 原因见 total_cost_detail; 0 = 真的花了 0 元); "
        "写进去之后不再改写; 金额用 NUMERIC 不用浮点: 浮点算钱会丢分",
    ),
    Column(
        "total_cost_detail",
        JSONB,
        nullable=True,
        comment="这笔钱的来路 (与 total_cost 同生共死): 算得出来时记哪一套价 "
        "(peak / valley) + 三个单价 + 三档用量; 算不出来时记原因 (没配价 / 缺哪个"
        "分量 / 日历过期). 只留一个数字的话事后没人能验算, 连「这是峰价还是谷价"
        "算的」都看不出来 (ticket 28)",
    ),
    Column(
        "turn_count",
        Integer,
        nullable=False,
        server_default="0",
        comment="已执行的 Turn 数 (断点续跑时是累计值, 不从头数)",
    ),
    Column(
        "error",
        JSONB,
        nullable=True,
        comment="失败原因 (结构化: code + message), 供审计与降级判断",
    ),
    Column(
        "last_checkpoint_id",
        String(128),
        # `use_alter=True`: 这一列与 `checkpoints.run_id` 互为外键, 两张表之间**无解
        # 的环**. SQLAlchemy 因此不能靠拓扑排序决定建表顺序 —— 标上它, 这条约束会
        # 被推迟成建表之后的 `ALTER TABLE` (不标的话 `create_all` 只报一条
        # 「unresolvable cycles」警告, 然后**跳过**这条约束: 库里少一个外键, 而
        # 没有任何地方会红). 迁移脚本里本来写的就是 ALTER, 不受影响.
        ForeignKey(
            "charagent_checkpoints.checkpoint_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        comment="这一段运行落的**最后一帧** (快照); 顺它的 parent_id 往回走就是本次"
        "运行落的全部帧 —— NULL = 没配快照存储 (或一帧都没落成)",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时刻",
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最后更新时刻",
    ),
    Column(
        "finished_at",
        DateTime(timezone=True),
        nullable=True,
        comment="结束时刻; NULL 表示还在跑 (或没跑到终点就丢了)",
    ),
    # 同一个 request_id 只能有一次执行 —— 这是幂等的兜底: 应用层先查后插有并发
    # 窗口, 唯一约束才是真正不会漏的那道闸.
    UniqueConstraint("request_id", name="uq_charagent_runs_request_id"),
    Index("ix_charagent_runs_thread_created", "thread_id", "created_at"),
    # 表级注释 (会变成库里的 COMMENT ON TABLE)
    comment="一次执行: 用户点一次「发送」到 agent 答完, 有自己的状态机与账目",
)

messages = Table(
    "charagent_messages",
    metadata,
    Column("message_id", String(128), primary_key=True, comment="消息编号"),
    Column(
        "thread_id",
        String(128),
        ForeignKey("charagent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话",
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("charagent_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        comment="归属哪次执行; NULL = 不是 agent 跑出来的 (比如人工客服接管后的回复)",
    ),
    Column(
        "role",
        String(_ENUM_LEN),
        nullable=False,
        comment="发言者 (MessageRole): user / assistant / tool / system",
    ),
    Column(
        "content",
        Text,
        nullable=True,
        comment="文本内容 —— assistant 的最终答复就在这里",
    ),
    Column(
        "reasoning",
        Text,
        nullable=True,
        comment="assistant 的思维链 (reasoning_content): 存下来供前端折叠展示与审计. "
        "注意这与 wire 历史的回填是两码事 —— 前者是「给人看」, 后者是「喂模型」",
    ),
    Column(
        "tool_call_ids",
        JSONB,
        nullable=False,
        server_default="[]",
        comment="这条 assistant 消息发起了哪些工具调用 (列表, 保持并行语义 #1)",
    ),
    Column(
        "hidden",
        Boolean,
        nullable=False,
        server_default="false",
        comment="内部消息: true = 不展示给前端 (续写指令 / 压缩摘要 / 工具回填 / "
        "带工具调用的中间轮). 会话历史接口只取 false 的那些 (见 conversation.py)",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="产生的时刻 (会话历史按它排序)",
    ),
    Index("ix_charagent_messages_thread_created", "thread_id", "created_at"),
    # 表级注释 (会变成库里的 COMMENT ON TABLE)
    comment="消息: 一问一答里的那一条 (hidden=False 的才给前端看)",
)

tool_calls = Table(
    "charagent_tool_calls",
    metadata,
    Column(
        "run_id",
        String(128),
        ForeignKey("charagent_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
        comment="归属哪次执行 (复合主键的三分之一)",
    ),
    Column(
        "message_id",
        String(128),
        ForeignKey("charagent_messages.message_id", ondelete="CASCADE"),
        primary_key=True,
        comment="是哪条 assistant 消息发起的 (复合主键的三分之一, 且**不能为空**)",
    ),
    Column(
        "tool_call_id",
        String(128),
        primary_key=True,
        comment="模型给的调用编号 (复合主键的三分之一). **为什么主键要三列**: "
        "真实上游每轮都从 call_0 重新编号 —— 同一个 run 里会出现好几条「call_0」, "
        "单列主键第二次就撞; 而加上 run_id 只解决了「跨 run」, 同一个 run 的多轮"
        "之间仍会撞. 真正唯一的身份是「哪条 assistant 消息发起的这一次调用」, "
        "所以 (run_id, message_id, tool_call_id) 三列才是完整的键",
    ),
    Column("tool_name", String(128), nullable=False, comment="调用的工具名"),
    Column(
        "arguments",
        Text,
        nullable=False,
        server_default="",
        comment="模型填的参数, **原样 JSON 字符串** (不预解析): 畸形 JSON 正是 "
        "自纠错路径的信号 (#2), 解析了反而丢证据. 想查字段用 arguments::jsonb",
    ),
    Column(
        "status",
        String(_ENUM_LEN),
        nullable=False,
        server_default="pending",
        comment="执行状态 (ToolCallStatus): pending / running / succeeded / failed "
        "/ cancelled / needs_approval (HITL 挂起等人工批准 #25)",
    ),
    Column(
        "result",
        JSONB,
        nullable=True,
        comment="执行结果 (成功) 或可操作错误信息 (#2: 说清字段格式问题, 不甩 422)",
    ),
    Column(
        "duration_ms",
        Integer,
        nullable=True,
        comment="耗时毫秒; NULL = 没跑到计时那一步",
    ),
    Column(
        "approved_by",
        String(128),
        nullable=True,
        comment="HITL 审批人 (#25); NULL = 无需审批或还没批",
    ),
    Column(
        "approved_at",
        DateTime(timezone=True),
        nullable=True,
        comment="HITL 审批时刻 (#25)",
    ),
    Column(
        "approval_prompt",
        Text,
        nullable=True,
        comment="挂起时**给用户看的那句话** (#25, 业务给的话术, 框架只搬运): "
        "刷新页面之后靠它重建确认卡 —— 缺了它, 用户看到一张没有字的卡, "
        "永远不知道该确认什么. NULL = 这一条不是要人批的调用",
    ),
    Column(
        "approval_needs",
        JSONB,
        nullable=True,
        comment="挂起时**还缺什么** (#25, 机器可读的短名字数组, 如 "
        '["payment_password"]): 前端按它决定卡片上要不要渲染输入框. '
        "框架不解释这些名字的含义, 原样透传; NULL = 这一条不是要人批的调用",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="发起时刻",
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="状态最后变化时刻 —— 追问「这条调用卡了多久」看它",
    ),
    Index("ix_charagent_tool_calls_run_created", "run_id", "created_at"),
    # 表级注释 (会变成库里的 COMMENT ON TABLE)
    comment="工具调用: 模型「我要去查一下」的那一下 —— 参数 / 状态 / 结果 / 审批",
)

checkpoints = Table(
    "charagent_checkpoints",
    metadata,
    Column("checkpoint_id", String(128), primary_key=True, comment="快照编号"),
    Column(
        "thread_id",
        String(128),
        # 这条外键是 ticket 24 补的: 原先只有注释说它是「分区键」, 却没有约束保证
        # 那个会话真的存在 —— 于是「只在库里跑快照存储、不配记录层」时, 帧会挂到
        # 一个不存在的会话上, 而且谁也发现不了.
        #
        # 代价是一条**行为契约**: thread_id 不能为空, 所以只能 CASCADE (不能像
        # run_id 那样 SET NULL), 于是**写帧之前会话行必须已经存在**. 快照存储
        # (PostgresCheckpointSaver) 自己不知道 tenant_id / user_id, 没法替调用方
        # 补建会话行 —— 直接用它的人要先自己建一行 `charagent_threads`
        # (ChatSession 那条路不必操心: `_begin_run` 早于 `loop.run`).
        ForeignKey("charagent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话 (分区键: 一个会话的所有快照排成一条线); "
        "会话删掉, 它的快照一并删掉 (CASCADE)",
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("charagent_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        comment="归属记录层的哪一行账 (charagent_runs); NULL = 不属于任何一行 "
        "(没配记录层的进程 / 那一轮没记上账 / 老帧)",
    ),
    Column(
        "loop_id",
        String(128),
        nullable=False,
        comment="哪一次循环执行存下的 (一次循环执行的几帧共享; 续跑沿用)",
    ),
    Column(
        "turn_number",
        Integer,
        nullable=False,
        comment="存下它时已经跑完几轮 (「执行到哪一步」#5)",
    ),
    Column(
        "schema_version",
        Integer,
        nullable=False,
        comment="快照内容的格式版本号 (#5 向前兼容): 老快照读回来要按它升级",
    ),
    Column(
        "state",
        JSONB,
        nullable=False,
        comment="进度: 消息历史 + 计数器 + 挂起点 (恢复才用, 序列化协议见 "
        "checkpoint/serialization.py)",
    ),
    Column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
        comment="观察值: 来源 + 本轮 token 与耗时 + 工具 + 结束原因 (给人看, "
        "回放调试用; v3 起从 state 里分出来)",
    ),
    Column(
        "parent_id",
        String(128),
        ForeignKey("charagent_checkpoints.checkpoint_id", ondelete="SET NULL"),
        nullable=True,
        comment="上一帧的编号 —— 从头排到尾是一条链; 从老快照恢复则在那里岔出"
        "新分支 (time-travel #5). NULL = 这条线的头一份",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="存下的时刻 (带时区; 排序与回溯都看它)",
    ),
    # 「按会话翻历史 / 取最新一帧」是仅有的两种查法, 一条复合索引就够.
    # created_at + checkpoint_id 一起排, 保证同一毫秒存下的两帧顺序也确定.
    Index(
        "ix_charagent_checkpoints_thread_created",
        "thread_id",
        "created_at",
        "checkpoint_id",
    ),
    # 表级注释 (会变成库里的 COMMENT ON TABLE). 位置与 Column / Index 无关, 但
    # **关键字参数只能排在所有位置参数之后** —— 插在中间是语法错误.
    comment="会话快照: 一帧一行, 全历史都在 (agent/loop.py 每 Turn 末尾落一帧)",
)

# 幂等登记簿 (#17): 一次**真实动作**一行 —— 这张表挡的不是「重复建 run」
# (`runs.request_id` 管那件事, 见那张表的唯一约束), 而是「**同一次工具调用被
# 执行两遍**」(HITL 的挂起-恢复被触发两次就是这条路径).
#
# **为什么这张表没有实体** (entities.py 里五实体没有它): 它「业务视角」的那一半
# 短到只有一句话 —— 「这个动作做过了吗」; 而这句话已经由 `retry/` 的
# `IdempotencyStore` 协议 + `ClaimResult` 说明白了. 再包一层实体只会多一个没人
# 读的中间形状.
#
# **为什么键是主键而不是自增 + 唯一索引**: 键就是身份, 没有别的身份. 而认领的
# 原子性恰好落在主键上 —— `ON CONFLICT (key)` 是「谁先插进去谁拿到执行权」那句
# 话在数据库层的写法, 比「先查后插」多一道真正不会漏的闸 (与 `runs.request_id`
# 那条唯一约束同一条理由).
idempotency_keys = Table(
    "charagent_idempotency_keys",
    metadata,
    Column(
        "key",
        String(255),
        primary_key=True,
        comment="幂等键本身 —— 长度上限 255 与 retry/idempotency.py 的 "
        "MAX_KEY_LENGTH 同源 (那个模块构造时就挡掉超长与白名单外的字符, 于是这一列"
        "只会收到合法值)",
    ),
    Column(
        "status",
        String(_ENUM_LEN),
        nullable=False,
        # 刻意**不给默认值**: 这一列只有认领那一个写入方, 而它一定显式写.
        # 留个默认值反而是个静默陷阱 —— 谁插了一行不带状态, 默认「在途」会把
        # 这把键永久挡住 (没有任何地方会红).
        comment="认领状态 (ClaimStatus): in_progress = 有人在做 (重复请求会被挡回), "
        "completed = 已做完且结果在 result 列. 三态里的 claimed 是**本次认领成功的"
        "答复**, 不是存下来的状态 —— 谁拿到了执行权, 写下来的都是 in_progress",
    ),
    Column(
        "result",
        JSONB,
        nullable=True,
        comment="完成时的结果 (complete(key, result) 存下的东西, 可 JSON 序列化); "
        "NULL = 还没做完 (在途)",
    ),
    Column(
        "expires_at",
        DateTime(timezone=True),
        nullable=True,
        comment="过期时刻 (NULL = 永不过期, 本框架的默认). **过了这一刻同一个"
        "键可以重新认领** —— 那时它已经不是同一次操作了, 与「永不过期」是两种"
        "口径, 这里取前者. 它只在**认领**那一刻定下, 之后完成与释放都不改它"
        " (唯一例外是没有认领记录的补登, 那时按当下的配置现算一次)",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本次认领时刻 (过期被重新认领时随之改写 —— 那一行从此描述的是新的"
        "那一次操作)",
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="状态最后变化时刻 (认出「这条卡了多久」看它)",
    ),
    comment="幂等登记簿: 一条键一行 —— 认领过 / 正在做 / 做完了结果是啥 (#17 的"
    "持久化底座, 调用方是 HITL 的挂起-恢复)",
)

# 六张业务表按依赖顺序排好, 建表时直接按这个顺序跑 (被引用的先建, 否则外键指向
# 一个还不存在的表). SQLAlchemy 的 metadata.sorted_tables 也能算出来, 这里显式
# 列一份是为了让「谁依赖谁」在文件里一眼可见.
#
# `charagent_migrations` (alembic 的版本表) **不在这份清单里**: 它是工具的表,
# 由 alembic 自己建自己管, 运行时建表 (create_all) 不该去碰它 —— 否则一张空表
# 会让 alembic 把「版本表在、但没有行」读成「这个库还停在 base」. 理由见模块
# docstring.
ALL_TABLES: tuple[Table, ...] = (
    threads,
    runs,
    messages,
    tool_calls,
    checkpoints,
    # 幂等登记簿没有任何外键 (它不挂在会话 / 运行下面 —— 键是调用方拼出来的),
    # 于是它排在哪都行, 放最后
    idempotency_keys,
)

# 表名清单 (测试与运维脚本按名字找表用; 顺序与 ALL_TABLES 一致)
TABLE_NAMES: tuple[str, ...] = tuple(table.name for table in ALL_TABLES)

# 表名常量: 给「只要名字、不要表对象」的地方用 (测试里裸 SQL 查表、conftest 清理
# 数据), 从 Table 对象上取而不是再抄一遍字面量.
#
# 只给 checkpoints 添这个常量, 是因为**只有它在代码里被这么用**: 其余四张表的
# 名字出现在用例里时是**故意写字面量**的 —— test_db_schema.py 用硬编码的完整
# 表名清单当契约断言, 若改成引用本文件常量, 常量本身写错就没人发现了.
# 其余四张表若要按名字取, 用 `metadata.tables` 或 `table.name`, 不必在这里加常量.
CHECKPOINTS_TABLE_NAME = checkpoints.name
