"""五个实体 + 四个状态枚举 (P0 一次定死, difficulties #12 实体部分).

一句话理解: 这是**数据库的户口本** —— 会话 (Thread)、运行 (Run)、消息
(Message)、工具调用 (ToolCall)、快照 (CheckpointRow) 各是什么、有哪些字段、
状态能怎么变, 全写在这里. 表长什么样在 schema.py, 这里写的是「怎么读写它」
以及「哪些取值是合法的」.

**业务含义**与**库里的形状**的关系: 前者是「这个词在业务上指什么」, 这里
是「它**落到库里是什么样**」. 两边必须一一对应, 有出入就是有一处过时了.

为什么用 StrEnum 而不是裸字符串: 状态名散落在代码里当字面量写, 拼错一个字母
编译器不管、测试不报、库里悄悄多出一个谁也不认识的状态. 用枚举则:
① 拼错名字当场 ImportError/AttributeError; ② IDE 能补全;
③ `set(RunStatus)` 就是「全部合法取值」, 测试可以逐条对齐文档.
`StrEnum` 的成员**本身就是字符串**, 所以直接塞进 SQLAlchemy 的 String 列、
直接 `== "running"` 比较都没问题 —— 既是枚举又不影响存库.

**四个状态枚举的取值来源** (测试 test_db_entities.py 会逐条对齐):

- ThreadStatus      会话状态: 与 Thread 实体的 status 字段一一对应
- RunStatus         运行状态机 (#16): 8 态, 有合法迁移规则 (见 state.py)
- MessageRole       发言者: 与 wire 消息的 role 取值一致 (user/assistant/tool/system)
- ToolCallStatus    工具调用状态: 含 needs_approval (HITL 挂起 #25)

**时间戳得由调用方传** (2026-09-14 审计澄清): 这些类是用 `__table__` 映射到 Core
表的, 那种写法**拿不到 Python 侧的默认值** (SQLAlchemy 不会把 `Column(default=...)`
搬到 ORM 类上). 于是 `Thread(thread_id=...)` 造出来的对象 `created_at` 是 `None`,
直接 INSERT 会撞 NOT NULL. 实际写法有两种, 都能用:

- **仓储路径** (推荐): `ThreadsRepository.add(...)` 这类方法自己补时刻, 调用方
  不用管.
- **手工造实体**: 自己给 `created_at`, 且**必须带时区** (`datetime.now(UTC)`) ——
  库里全是 TIMESTAMPTZ, 塞个裸 datetime 进去, 读回来会变成「不知道哪儿的几点」,
  排序就可能悄悄错位.

库端还有一层兜底 (`server_default=now()`): 绕开 Python 直接写裸 SQL 时, 不传
时间列也能落库.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import DeclarativeBase

from CharAgent.db.schema import (
    checkpoints,
    messages,
    runs,
    threads,
    tool_calls,
)


class ThreadStatus(StrEnum):
    """会话状态: 还能聊 / 已归档 / 已转人工 (对应 Thread 实体的 status)."""

    ACTIVE = "active"  # 正常进行中
    CLOSED = "closed"  # 已结束归档 (用户手动关闭或长时间无活动)
    ESCALATED = "escalated"  # 已转人工 (#19): 有一段对话由人工客服接管了


class RunStatus(StrEnum):
    """运行状态机 (#16): 一次执行的八种状态, 有合法迁移规则 (见 state.py).

    由**执行层**推进, 不是模型建议的 —— 模型只能决定「这一轮要调工具 / 给答案」,
    把状态从 running 改成 waiting_tool 是框架根据响应做的判定.

    八态为什么要有 wait 与 retry 这几档 (而不是「跑着 / 成了 / 挂了」三态):
    前端要能回答「它现在到底在干嘛」—— 等工具、等人工审批、还是在重试上游,
    这三种「没动静」对用户的含义完全不同 (等审批要提示去审批台, 重试则是自动
    的, 稍等就好).
    """

    CREATED = "created"  # 已建记录, 还没开始跑
    RUNNING = "running"  # 模型正在决策 (调用中)
    WAITING_TOOL = "waiting_tool"  # 模型已决定调工具, 正在执行工具
    WAITING_USER = "waiting_user"  # 挂起等人 (#25 HITL 审批): 不消耗 token, 可能很久
    RETRYING = "retrying"  # 上游失败, 正在退避重试 (#13): 自动的, 稍等即可
    FAILED = "failed"  # 失败终止 (终态)
    FINISHED = "finished"  # 正常结束 (终态)
    CANCELLED = "cancelled"  # 被取消 (#18 kill switch, 终态): 可从任意非终态进入


class MessageRole(StrEnum):
    """发言者: 取值与 wire 消息的 role 一致, 于是两个世界不用翻译 (#10).

    - USER: 用户说的话. **判定「谁说的」只能看这一行是不是真由用户输入产生** ——
      模型有时会自己编出一段「用户说...」, 那种消息的角色是 assistant, 绝不能
      当 user 存 (否则前端会把它渲染成用户真的说过, 见 conversation.py).
    - ASSISTANT: 模型说的话 (可能是最终答复, 也可能是边思考边调工具的那一轮).
    - TOOL: 工具执行结果回填给模型的那条消息 (wire 历史里的 role=tool).
    - SYSTEM: 系统注入的指令 (续写指令 / 压缩摘要 / 角色设定), 不是任何人说的.
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class ToolCallStatus(StrEnum):
    """工具调用状态: 六个取值含 needs_approval (HITL 挂起 #25)."""

    PENDING = "pending"  # 模型刚发起, 还没开始执行
    RUNNING = "running"  # 执行中
    SUCCEEDED = "succeeded"  # 执行成功
    FAILED = "failed"  # 执行失败 (错误信息可操作 #2, 会回填给模型自纠错)
    CANCELLED = "cancelled"  # 被取消 (#18): 高危动作被人工拒绝, 或整个 run 被叫停
    NEEDS_APPROVAL = "needs_approval"  # 挂起等人工批准 (#25): 退款这类高危动作


class Base(DeclarativeBase):
    """所有实体的基类, 并**认领 schema.py 那份 metadata** 作为自己的表定义.

    为什么要显式指过去 (而不是让 Base 自己新建一份 metadata): 表定义与迁移体系
    必须共用同一份 metadata —— alembic 的 autogenerate 拿它当「代码侧应该长什么
    样」的基准, 而快照存储 (checkpoint/postgres.py) 直接操作那几张 Table 对象.
    若 Base 自建一份, 就会出现「同一个表名挂两份定义」, 那正是本层要消灭
    的东西.

    用可读的约束名 (naming_convention, 见 schema.py): 数据库自动起的名字是随机
    串, 迁移脚本里想改一个约束就得先去库里查它叫什么.
    """

    # 自己不映射任何表 (基类不是实体): 五张业务表分别由下面的五个子类认领.
    # (`charagent_migrations` 那张版本表**没有实体** —— 它是 alembic 的设施
    # (ticket 24 起兼作审计表), 由它的钩子直接写, 没有业务代码去读它.)
    # 少了这一行, SQLAlchemy 会试着给基类也找一张表, 找不到就报错.
    __abstract__ = True
    # 认领 schema.py 那份 metadata (表定义与迁移体系的唯一来源)
    metadata = threads.metadata


class Thread(Base):
    """会话 (Thread): 一段对话的容器, 也是数据隔离的单位.

    为什么它是最外层的实体: 所有东西 (运行 / 消息 / 工具调用 / 快照) 都挂在
    一个会话下 —— 就像一棵树的根. 于是「删掉一个会话」= 删掉它名下的一切
    (库里靠外键 CASCADE 兜住, 不必应用层逐个删).

    attributes:
        thread_id: 全局唯一编号 (uuid4 hex), 也是快照存储的分区键.
        tenant_id: 租户 —— **多租户隔离的过滤键 (#32)**: P1 起所有查询强制带上
            它, 否则 A 公司能查到 B 公司的会话.
        user_id: 会话属主 —— 决定「谁能看到这段对话」.
        title: 会话标题 (由首条用户消息生成; 前端左侧会话列表显示它).
        status: 见 ThreadStatus.
        created_at / updated_at: 建的时刻 / 最后活动的时刻 (列表按后者倒序).
        pinned_at: 置顶时刻; None = 未置顶 —— 列表把置顶的排在最前 (#20).
        deleted_at: 删除时刻; None = 还在 —— 有值表示用户把它从列表里删掉了.
            **软删**: 行与它名下的消息都留着 (成本记账与排查要用), 只是不再列出.
    """

    __table__ = threads

    def __repr__(self) -> str:
        """调试用的一行摘要 (不打印全部字段: 日志里刷屏)."""
        return (
            f"Thread(thread_id={self.thread_id!r}, tenant_id={self.tenant_id!r}, "
            f"user_id={self.user_id!r}, status={self.status!r})"
        )


class Run(Base):
    """一次执行 (Run): 用户点一次「发送」到 agent 答完.

    会话 (Thread) 是长期的, 运行 (Run) 是一次性的 —— 一段对话里可以有几十次
    运行, 每次都有自己独立的状态机、token 账、失败原因.

    attributes:
        run_id: 全局唯一编号.
        thread_id: 属于哪段对话.
        status: 状态机 (见 RunStatus 与 state.py 的合法迁移表).
        request_id: 幂等键 (#17): 客户端重试时带同一个值, 服务端发现已经跑过就
            直接返回已有结果, 不重跑 (退款这种动作重跑就是事故).
        model / prompt_version: 这次用的模型与 prompt 版本 —— 版本化 (#40) 的
            基础: 回答质量掉了要能查出「是换了模型还是换了 prompt」.
            prompt_version 存的是提示词名 (如 "system/v2"), 与快照里那个引用的
            name 同源; 没配身份说明时为 NULL.
        total_tokens: 本 run 累计用量 (账单原值).
        total_cost / total_cost_detail: 本次运行的花费与它的来路 —— **收尾那一刻**
            按当时的价目表算好写入 (`db/cost.py`), 之后不再改写. 金额可空: NULL 表示
            那一刻没算出来 (原因在明细里: 没配价 / 缺哪一档 / 日历过期), 与 0 (真的
            花了 0 元) 是相反的结论; 峰谷价按本次运行的**开始时刻**判定 (ticket 28).
        input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
        cache_miss_tokens: total_tokens 的**归因拆解** (#34). 五列都可空 —— NULL
            表示上游一次都没上报过这个分量, 与 0 (报过、值就是零) 是两回事. 要算
            钱就按「缓存命中 / 未命中 / 输出」三档单价分别乘, 别拿 total_tokens 乘
            一个均价 (那会把三类不同价的 token 混成一个数); 推理那列**不参与乘法**
            (它是输出列的明细, 已含在输出价里, 见 db/cost.py).
        turn_count: 已执行的 Turn 数 (断点续跑时是累计值).
        error: 失败原因 (结构化 JSONB: code + message), 供审计与降级判断.
        last_checkpoint_id: 这一段运行落的**最后一帧**快照 (票 22); 顺它的
            parent_id 往回走就是本次运行落的每一帧 —— 「当时它看到了什么」与
            「花了多少」由此能对到同一件事上. NULL = 没配快照存储 (或一帧都没落成).
        created_at / updated_at / finished_at: 建 / 最后更新 / 结束的时刻;
            finished_at 为 NULL 表示还没跑到终点.
    """

    __table__ = runs

    @property
    def is_terminal(self) -> bool:
        """这次执行是不是已经结束了 (终态之后不会再变, 见 state.py).

        为什么做成属性而不是让调用方自己比: 「哪些状态算结束」这件事在 state.py
        里是权威定义 (TERMINAL_RUN_STATUSES), 各处自己写 `status in (...)` 迟早
        会漏掉一个.
        """
        # 延迟 import: state.py 要用这里的 RunStatus, 顶层互相 import 会成环
        from CharAgent.db.state import TERMINAL_RUN_STATUSES

        return RunStatus(self.status) in TERMINAL_RUN_STATUSES

    def __repr__(self) -> str:
        """调试用的一行摘要."""
        return (
            f"Run(run_id={self.run_id!r}, thread_id={self.thread_id!r}, "
            f"status={self.status!r}, turn_count={self.turn_count})"
        )


class Message(Base):
    """消息 (Message): 一问一答里的那一条.

    **本表存的是「会话」, 不是「transcript」** —— 这条分层决定「前端看到什么」,
    展开说:

    一次 agent 运行 (特别是带工具、带截断续写的) 产生的 wire 消息历史要比
    「一问一答」多得多: 里面有 system 续写指令、tool 回填结果、带 tool_calls 的
    中间轮、以及模型自己编出来的「用户说...」. 完整的 wire 历史**已经**存在
    快照里 (CheckpointState.messages, 恢复靠它), 不需要在这里再存一份.
    本表只回答一个问题: **前端点进一段对话, 应该看到什么**.

    于是 `hidden` 这一列承担全部裁量:
    - `hidden=False` —— 可以给用户看的: 用户真发的问题 + 最终答复正文.
    - `hidden=True` —— 内部件: system 指令 / tool 回填 / 带工具调用的中间轮 /
      压掉的过程叙述. 它们留着是为了「这个会话到底发生了什么」可查 (审计与
      排查), 但会话历史接口 (`MessagesRepository.list_conversation`) 不会取它们.

    分层规则不是拍脑袋定的, 全部收在 conversation.py 一处, 有专门用例钉住.

    attributes:
        message_id: 消息编号.
        thread_id: 属于哪段对话.
        run_id: 是哪次执行产生的; **NULL 表示不是 agent 跑出来的** —— 比如人工
            客服接管后手动回的那条 (接管台的 POST /reply).
        role: 发言者, 见 MessageRole.
        content: 文本内容; assistant 的最终答复就在这里.
        reasoning: 思维链 (reasoning_content). 存它是两件事:
            ① 前端折叠展示 (「它当时在想什么」) 与审计;
            ② 官方文档要求带 tools 的请求回填 wire 历史 (实测 2026-09-11 未强制,
            框架仍按文档执行以保留交错思考 —— 见 model 层).
            注意「存下来的理由」与「要不要给用户看」是两码事: 中间轮的 reasoning
            也存, 但那几行是 hidden=True.
        tool_call_ids: 这条 assistant 消息发起了哪些工具调用 (列表, 保持并行语义:
            同一轮的多个调用属于同一条消息, 不拆开).
        hidden: 见上文的表级说明.
        created_at: 产生的时刻 (会话历史按它排序).
    """

    __table__ = messages

    def __repr__(self) -> str:
        """调试用的一行摘要 (content 可能很长, 只给个预览)."""
        preview = (self.content or "")[:24]
        return (
            f"Message(message_id={self.message_id!r}, role={self.role!r}, "
            f"hidden={self.hidden}, content={preview!r})"
        )


class ToolCall(Base):
    """一次工具调用 (ToolCall): 模型「我要去查一下」的那一下.

    它是「模型说的」与「实际做的」之间的桥: 参数是模型填的 (原样存, 不预解析),
    结果是工具返回的 (成功或可操作的失败原因). 高危动作 (退款) 会先停在这里
    (status = needs_approval) 等人批准, 批准后才真的执行 (#25).

    **主键是 (run_id, message_id, tool_call_id) 三列** —— 少一列都会撞键,
    理由见 schema.py 那段注释 (上游每轮从 call_0 重新编号).

    attributes:
        run_id / message_id / tool_call_id: 见上 (三列合起来才是完整身份).
        tool_name: 调用的工具名.
        arguments: 模型填的参数, **原样 JSON 字符串** (畸形 JSON 正是自纠错路径的
            信号, 解析了反而丢证据).
        status: 见 ToolCallStatus.
        result: 执行结果 (成功) 或可操作错误信息 (失败; 会说清字段格式该是什么样,
            而不是甩一句 422 —— 模型要靠这句话自己改对).
        duration_ms: 耗时; 追问「这个工具为什么慢」看它.
        approved_by / approved_at: HITL 审批人与时刻 (#25); NULL = 无需审批或还没批.
        created_at / updated_at: 发起 / 状态最后变化的时刻.
    """

    __table__ = tool_calls

    def __repr__(self) -> str:
        """调试用的一行摘要."""
        return (
            f"ToolCall(run_id={self.run_id!r}, tool_call_id={self.tool_call_id!r}, "
            f"tool_name={self.tool_name!r}, status={self.status!r})"
        )


class CheckpointRow(Base):
    """快照记录 (CheckpointRow)—— **数据库视角的那一半**.

    一句话理解: 这是游戏存档在库里的那一行. 往哪儿存、怎么读回来由
    `checkpoint/postgres.py` 负责; 这里只描述「那一行长什么样」.

    **为什么它叫 CheckpointRow 而不是 Checkpoint**: `checkpoint` 包里已经有一个
    `Checkpoint` (数据类, agent loop 运行时手里拿着的那个对象). 两个同名类会让人
    以为它们是一个东西 —— 其实职责完全不同: 那个是**运行时的快照对象**, 这个是
    **库里的那一行**. 加 Row 后缀, 一眼分得清谁是谁, 也顺带说清层的关系:
    `checkpoint/postgres.py` 负责把这一行翻译成那个对象 (那里有 codec).

    **为什么本类不提供「转成 Checkpoint 对象」的方法**: 那会让 db 包反过来
    import checkpoint 包, 而 checkpoint 包现在正 import 着本包的 schema (它要用
    这张表的定义) —— 两边互相 import 就是死循环. 翻译留在 checkpoint 那边,
    那是两侧唯一的交汇点.

    attributes:
        checkpoint_id / thread_id / loop_id / run_id / turn_number / schema_version /
        state / metadata / parent_id / created_at: 语义见
            checkpoint/utils/types.py 的 Checkpoint 数据类 (两边字段一一对应).
            **两个编号别混** (票 22 改的): `loop_id` 是「哪一次循环执行落下的」
            (一次循环执行的几帧共享), `run_id` 是「属于本表所属的哪一行账」
            (指向 charagent_runs, 可空).
    """

    __table__ = checkpoints

    def __repr__(self) -> str:
        """调试用的一行摘要."""
        return (
            f"CheckpointRow(checkpoint_id={self.checkpoint_id!r}, "
            f"thread_id={self.thread_id!r}, turn_number={self.turn_number}, "
            f"parent_id={self.parent_id!r})"
        )
