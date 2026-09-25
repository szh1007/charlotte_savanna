"""客服服务的装配: 一处身份来路 + 一处会话装配, 命令行与 HTTP 两个入口共用.

一句话理解: 这是「跟客服说话」里**与入口无关**的那一半. CLI 与 server 的差别
只在两头 —— 前面「身份从哪来」、后面「话怎么说给谁听」; 中间从身份到一台能问答
的机器, 两个入口走的必须是同一段代码 (本模块).

| 归本模块 (两处共用) | 归入口 (各自独有) |
|---|---|
| `build_context`: 买家身份 → `RunContext` | CLI: 解析 argv / 交互循环 / 终端渲染 |
| `MinimallService.session_for`: 上下文 → 会话 | server: HTTP 回调 / 进程生命周期 |
| `build_model_for` / `STARTUP_ERRORS` | |

**为什么非要拆出来**: 2026-09-19 吃过一次亏 —— 业务当初把框架的交互循环抄了一份
(约 94 / 130 行逐字相同), 抄完当场开始漂 (业务那份丢了快照帧数提示). 框架侧后来
的处理是把那一层上浮成公共 API, 业务改成子类只覆盖四个钩子. 本模块是同一件事在
「装配」上的做法, 判断标准只有一条 —— **换一个入口还要不要这段?** 要 → 放这里;
不要 → 留在各自入口. 有一条测试从两个入口各打一次, 断言落点是同一个函数 (不是
两份长得像的代码).

**身份从哪来仍然只有一处**: 只是那一处从「一个函数体」变成了 `build_context` 的
参数 —— CLI 传命令行参数, server 传请求头里的 `X-User-Id`. 工具闭包、提示词、
快照分区规则一律不动 (PRD §4.2 承诺的「后面一行不改」兑现的就是这一页).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from CharAgent.agent import (
    CalibratedTokenCounter,
    CompactionPolicy,
    GuardConfigError,
    LoopConfigError,
    LoopGuard,
    RunContext,
    TokenCounter,
    TrimAndSummarize,
)
from CharAgent.checkpoint import CheckpointError, CheckpointSaver
from CharAgent.client import ChatSession, CliOptions, build_model
from CharAgent.db import ConversationRecorder, PgDatabase, load_pricing
from CharAgent.db.errors import PricingNotReadyError
from CharAgent.hooks import HookRegistry
from CharAgent.model import ModelError
from CharAgent.model.protocol import ChatModel
from CharAgent.prompt import PromptError
from CharAgent.stream import EventSink
from CharAgent.tool import Tool
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import ContextConfig, MinimallConfigError
from CharApp.minimall.guardrail import WriteGuardrail
from CharApp.minimall.provider import (
    PAYLOAD_USER_ID,
    MinimallToolProvider,
    buyer_id,
)
from CharApp.minimall.redaction import redacting_sink

# 会话编号的第一段 (PRD §4.11: `业务:买家ID:对话ID`) —— 快照按它分区, 于是
# 多用户隔离是免费得到的: 换一个买家就是换一个分区, 谁也读不到谁的档.
CONVERSATION_PREFIX = "minimall"

# 身份里的「租户」两段 (ticket 17): 框架按它分区与过滤, 而这里就是 PRD §4.11 那
# 三段编号的第一段 —— **同一个买家在网页端与命令行是两个租户**.
#
# 为什么要分开 (而不是都用 "minimall"): 两边都是同一个人在说话, 但前端左栏该
# 显示的是「网页里聊过的会话」—— 命令行里敲的那些是开发者自己的调试痕迹, 不该
# 混进买家的会话列表. 分租户之后这件事**免费**成立 (框架的列表按 tenant 过滤),
# 业务侧不必再写一条「把 cli 的过滤掉」的规则.
TENANT_WEB = CONVERSATION_PREFIX
TENANT_CLI = f"{CONVERSATION_PREFIX}-cli"

# 一次运行最多几轮模型决策 (LoopGuard) —— 两个入口的默认值同源, 免得一边改了
# 另一边还是老数字. CLI 的 `--max-turns` 缺省值也读它.
DEFAULT_MAX_TURNS = 10

# 一次运行的另外两道刹车 (框架的 LoopGuard 支持, 之前只配了轮数):
#
#   - token 预算: 客服问答 6 万 token 绰绰有余. 不设的后果是**一次跑飞就烧一波**
#     —— 轮数上限拦不住"某一轮本身就很贵"(超长输入 / 模型话痨).
#   - 墙钟预算: 90 秒. 为什么是 90 而不是 BFF 那边的 120 (它等上游的最大耐心):
#     这里先到, 用户看到的是「这次查得太久超时了」这句能听懂的话; 让 BFF 先到的话,
#     他看到的是一句「回答中途断开了」—— 同一件事, 后者更像是我们挂了.
#
# 两条都是**两个入口共用**的字段默认值 (HTTP 与命令行一起受约束): 烧的是同一份
# API 账单, 没有理由只拦浏览器那一侧; 命令行要放开就把字段显式传 None.
DEFAULT_MAX_TOTAL_TOKENS = 60_000
DEFAULT_MAX_DURATION_SECONDS = 90.0

# 业务提示词: 目录 + 名字 + 清单. 盘上的位置是 `{目录}/{名字}/{版本}.prompt`
# (PLAN §3.3 的布局), 而框架取正文的规则是「{目录}/{名字}.prompt」—— 所以下面拼
# 的时候把版本接在名字后面, 目录层级因此是**落库方式**的一部分, 而不是文件名里的
# 一个装饰: 换一版是加一个文件, 旧的还在.
#
# 它为什么是 system 而不是别的名字: 这段正文最终进的就是会话历史的第一条
# `role: system` 消息 (见 `ChatSession`), 按**它在 wire 上的角色**命名, 比按业务
# 叫法命名更难搞错 —— 一个业务可以有若干份提示词 (客服 / 摘要 / 分类…), 但
# system 只有一条.
PROMPT_DIR = Path(__file__).resolve().parent / "prompt"
PROMPT_NAME = "system"

# 版本不再硬编码在这里, 而是由清单声明 (`manifest.yaml` 的 `default`) —— 见
# `resolve_prompt_version`. 这样「这一次跑的是哪一版」在盘上有一个**可读的**
# 答案, 而不再是散在 Python 常量里的一句话.
PROMPT_MANIFEST = PROMPT_DIR / "manifest.yaml"

# 清单里声明默认版本的那个字段名
MANIFEST_DEFAULT_KEY = "default"

# 启动期可能抛出的配置类错误 (命令敲错了 / 环境没配好 / 提示词不在), 都不是
# 「运行中出问题」—— 报一句人话就退出, 不打印 traceback. 六者没有共同祖先
# (业务 / 模型 / 快照 / agent / 提示词 各一族), 只能列成元组.
#
# 两个入口共用这一份: CLI 拿它兜住启动装配, server 拿它兜住进程启动 —— 会抛的
# 是同一批错误, 该说的也是同一句人话.
STARTUP_ERRORS: tuple[type[Exception], ...] = (
    MinimallConfigError,
    # 计价那条路的启动自检 (ticket 28): 价目表写错 / 日历过期
    PricingNotReadyError,
    ModelError,
    CheckpointError,
    LoopConfigError,
    GuardConfigError,
    PromptError,
)


def resolve_prompt_version(manifest: Path | None = None) -> str:
    """读清单, 定下这次用哪一版提示词 (**版本号的唯一出处**).

    为什么要有清单文件而不是一个常量: 版本号是评估的前置条件 —— 运行记录里不写
    「用了哪一版」, 跑分再高也不知道是谁的功劳 (PRD §4.8). 而版本写进文件之后,
    「当前用哪一版」这件事就不再需要改代码.

    Args:
        manifest: 清单文件; None 表示本业务目录下那一份 (测试用来指向临时文件).

    Returns:
        str: 版本号 (如 `"v2"`), 交给 `ChatSession` 拼成 `{名字}/{版本}`.

    Raises:
        MinimallConfigError: 清单不在 / 不是合法 YAML / 没有可用的 `default` /
            声明的版本在盘上没有对应的 `.prompt`. **读不到就报错, 不静默退回上一
            版** —— 与 `PromptNotFoundError` 同一条纪律: 静默退回会让一次「v2 的
            跑分」其实是 v1 的成绩, 而且没有任何地方会报警.
    """
    path = PROMPT_MANIFEST if manifest is None else manifest
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MinimallConfigError(f"读不到提示词清单 {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MinimallConfigError(f"提示词清单 {path} 不是合法的 YAML: {exc}") from exc
    version = data.get(MANIFEST_DEFAULT_KEY) if isinstance(data, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise MinimallConfigError(
            f"提示词清单 {path} 里没有可用的 {MANIFEST_DEFAULT_KEY}: "
            f"写一行 `{MANIFEST_DEFAULT_KEY}: v1` 指明当前用哪一版"
        )
    # 「声明了哪一版」与「那一版在不在盘上」是同一件事的两半, 所以一起判:
    # 只读清单的话服务进程会照常起来, 错误推到每个买家的第一次提问 (框架那边
    # `load_prompt` 才发现文件不在) —— 那就不叫启动期错误了.
    resolved = version.strip()
    prompt_file = PROMPT_DIR / PROMPT_NAME / f"{resolved}.prompt"
    if not prompt_file.is_file():
        raise MinimallConfigError(
            f"提示词清单声明的版本 {resolved!r} 在盘上没有对应的文件: 找的是 "
            f"{prompt_file}. 补上它, 或者把清单的 {MANIFEST_DEFAULT_KEY} 改成"
            f"已有的一版"
        )
    return resolved


def thread_id_for(user_id: int, conversation_id: str) -> str:
    """会话编号 = `业务:买家ID:对话ID` (PRD §4.11) —— 规则的唯一出处.

    带上买家 ID 之后, 「多用户各看各的」不需要任何额外设计: 快照本来就按会话
    编号分区, 分区键里已经含了身份. 第三段 (对话 ID) 决定「同一买家的哪一段
    对话」—— 换个值就是一段新对话, 历史不串台.
    """
    return f"{CONVERSATION_PREFIX}:{user_id}:{conversation_id}"


def build_context(user_id: int, conversation_id: str, *, tenant_id: str) -> RunContext:
    """买家身份 → 运行上下文; **买家身份从哪来, 全项目只有这一处**.

    CLI 从命令行参数取 (`--user-id`), 服务进程从 Django 转发的请求头取
    (`X-User-Id`) —— 两个入口各自只有一行「从哪取」, 取到之后走的是这里 (PRD
    §4.2). `provider.provide` 之后的一切 (工具、闭包、schema) 与身份来自哪儿
    完全无关; 框架也不解释载荷里是什么 (身份在那个闭包里, 不在参数表上).

    Args:
        user_id: 买家在商城里的 User ID.
        conversation_id: 会话编号的第三段 (同一买家的第几段对话).
        tenant_id: 这次是谁在用这个身份说话 (`TENANT_WEB` / `TENANT_CLI`).
            **必填 keyword**: 框架按它分租户存放与列出会话, 给个默认值等于让
            「忘了填」变成一段谁也看不见的会话 —— 与框架不给那两个字段默认值
            是同一条理由.

    Returns:
        RunContext: 会话编号 + 属主身份 (租户 / 买家) + 装着买家 ID 的载荷.
    """
    return RunContext(
        thread_id=thread_id_for(user_id, conversation_id),
        tenant_id=tenant_id,
        # 框架只把这个值当**过滤键** (字符串), 而买家 ID 在本业务里是整数:
        # 转换就放在这一处接缝上, 别处一律按一种形态说话 (载荷里仍是整数 ——
        # 那是业务自己的口径, 工具闭包按它取身份).
        user_id=str(user_id),
        payload={PAYLOAD_USER_ID: user_id},
    )


def build_model_for(options: CliOptions, writer: Callable[[str], Any]) -> ChatModel:
    """按配置造模型 (借框架的装配: 裸适配器 + 默认套一层重试包装).

    两个入口都从这里拿模型: CLI 的 `writer` 是终端 (重试提示打给用户看), server
    的是日志 (重试提示留给排查).
    """
    return build_model(options, writer)


def build_compaction_for(
    config: ContextConfig,
) -> tuple[CompactionPolicy, TokenCounter]:
    """压缩参数 → 框架的两个零件 (策略 + 估算器), **一次给一对**.

    为什么必须成对: 框架那边「只给 counter 不给 compactor」是配置错误 (`AgentLoop`
    构造期就报 —— 没人会去问一个不压缩的会话要估算). 成对构造之后这个中间态不存在.

    为什么估算器**每调一次新的**: `CalibratedTokenCounter` 的校准项是「**这个会话**
    上一次请求的真实 input_tokens 与那一份的启发式估算之差」, 它是会话级状态. 进程级
    共用一个的话, 两段对话会互相把对方的校准项改掉 —— 甲刚压完 (校准项按小视图算出
    来), 乙的账本立刻被判定「没超阈值」, 于是该压的不压. 装配处每次 `session_for`
    调一次, 正好一个会话一份.

    Args:
        config: 从环境变量读来的五个旋钮 (见 `config.ContextConfig`).

    Returns:
        tuple: (策略, 估算器) —— 直接喂给 `ChatSession` 的两个参数.
    """
    return (
        TrimAndSummarize(
            threshold_tokens=config.max_tokens,
            keep_recent_questions=config.keep_turns,
            watermark_ratio=config.watermark,
            tool_result_limit=config.tool_limit,
            summarize=config.summary,
        ),
        CalibratedTokenCounter(),
    )


@dataclass(frozen=True, slots=True)
class MinimallService:
    """客服服务的零件与装配 (一个进程一份, 两个入口共用同一段装配代码).

    attributes:
        client: 商城客户端 (一条连接池 + 一个令牌). **进程级共享** —— 一次会话
            里问十几句、一个进程里几十个买家, 都只用这一条连接池 (见 client.py).
        model: 模型适配器 (含重试包装), 同样进程级共享.
        saver: 快照后端 (按 thread_id 分区, 一个进程一个).
        database: 记录表那条线的库入口 (ticket 17); None 表示这个进程不记账 ——
            会话照常能问答, 只是「重启后拿回历史」与「会话列表」没有落点.
            给了它就顺便决定了两件事: 每轮问答的账写进记录表 (见 db/recorder.py),
            以及服务进程多出几条会话路由 (列表与搜索 + ticket 20 的改名 / 置顶 /
            删除).
            **注意 `saver` 是 Postgres 时 `None` 是条被堵死的组合** (ticket 24):
            帧的 `thread_id` 指向记录层的 `charagent_threads`, 而不记账就没人建
            那一行, 第一句问话会以外键失败告终. 上面那句「照常能问答」只在
            memory / redis 快照后端下成立.
        model_name: 模型名覆盖; None 表示听 .env 的 DEEPSEEK_MODEL_NAME.
        thinking: 思考模式开关; None 表示不传 (上游默认开启). 服务端从
            `CHARAPP_THINKING` 读, 命令行入口从 `--no-thinking` 读 (两边都不填时
            语义相同: 让上游自己决定).
        max_turns: 轮数上限 (防跑飞).
        max_total_tokens: 单次运行的 token 预算; None 表示不限.
        max_duration_seconds: 单次运行的墙钟预算 (秒); None 表示不限.
        compaction: 上下文压缩的五个旋钮 (`config.ContextConfig`); **None 表示不压缩**
            —— 会话照常问答, 只是每一轮都把全量历史重发一遍 (与从前逐字一样).
            生产两个入口都从 `CHARAPP_CONTEXT_*` 读出来给它, 于是默认那套值也是
            显式的配置, 而不是"没人配就没有".

    三个零件都是**进程级**的, 所以谁建谁关: 建它的人在进程退出时调 `aclose()`
    (server 那侧); CLI 只有一次会话, 它沿用既有收尾 (`ChatSession.aclose()` 关的
    正是同一个模型与同一个存储, 再加客户端自己关).
    """

    client: MinimallClient
    model: ChatModel
    saver: CheckpointSaver
    database: PgDatabase | None = None
    model_name: str | None = None
    thinking: bool | None = None
    max_turns: int = DEFAULT_MAX_TURNS
    max_total_tokens: int | None = DEFAULT_MAX_TOTAL_TOKENS
    max_duration_seconds: float | None = DEFAULT_MAX_DURATION_SECONDS
    # 名字避开 `context`: 这个类的方法里已经有 `context: RunContext` (运行上下文),
    # 两者并存时「self.context」与「context」指的是两样东西 (CLI 那边已经被迫改过
    # 一次局部变量名) —— 而「压缩」正是框架对这个能力的叫法 (agent/compaction.py).
    compaction: ContextConfig | None = None

    def __post_init__(self) -> None:
        """装配那一刻就把计价那条路**验通** (ticket 28 业务侧要的启动自检).

        验什么: 价目表读得成吗; 若它按峰谷计价, **今天这一刻判得了峰谷吗** (中国
        日历的数据每年 11 月前后随依赖更新, 过期了就判不了).

        为什么在业务侧再验一遍 (框架的记录员构造时也会验): 记录员要等第一句问话
        才建出来, 而这里验的是**进程启动**那一刻 —— 起不来比起得来再报错更早、
        更省事. 两个入口 (CLI / server) 共用这一处装配, 所以两边的进程都拦得住.

        只记账的进程才验 (`database is None` = 不记账 = 没有金额这回事): 那种部署
        不该被一个它用不上的日历拦住.

        Raises:
            PricingNotReadyError: 价目表写错, 或配了峰谷价却判不了今天.
        """
        if self.database is None:
            return
        load_pricing()

    async def session_for(
        self, context: RunContext, *, event_sink: EventSink, redact: bool
    ) -> ChatSession:
        """把零件装成一台能问答的机器: 上下文 → 工具 + 护栏 + 记录员 → 会话.

        这是**唯一一处装配**: 命令行与 HTTP 两个入口都经过它, 于是「工具装了、
        护栏没挂」或「网页版记了账、命令行没记」这类半边生效不会发生.

        顺序如实反映依赖: 先拿上下文换工具 (提供者是异步的), 再把工具与护栏一起
        交给会话 —— 框架的 `ChatSession` 至今不知道 `RunContext` 存在 (见
        `agent/provider.py`); 业务借它的 `hooks=` 挂插件、借 `recorder=` 记账.

        为什么要收一个 `event_sink`: 事件的出口在**会话构造时**就定死了, 而一个
        会话要连续服务很多次运行. HTTP 那侧由框架按运行分流 (它递进来的是一条
        常驻路由), CLI 那侧是终端渲染 —— 业务只管转交, 不必知道它是什么.

        Args:
            context: 这次运行的上下文 (身份在载荷里).
            event_sink: 事件出口 (框架给的路由或终端的渲染器).
            redact: 这个出口是不是**浏览器** (`redaction.redacting_sink`).
                **必填 keyword, 不给默认值**: 这件事只有入口知道 (框架递过来的
                是一个 `EventSink`, 它长得跟终端渲染器一模一样), 而默认值会把
                「忘了选」变成一次静默的泄漏 —— 第三个入口接上来时, 漏了它就
                是工具参数原文直出浏览器 (ADR-0003 那条保证的正是这一跳).
                Web 入口给 True, CLI 入口给 False (出口是开发者自己的终端).

        Returns:
            ChatSession: 装好的会话 (工具 / 提示词 / 模型 / 快照 / 快照分区).

        Raises:
            MinimallConfigError: 载荷里没有买家身份 (装配时忘了放).
        """
        tools: Sequence[Tool] = await MinimallToolProvider(self.client).provide(context)
        # 护栏挂在这**唯一一处装配**上: 命令行与 HTTP 两个入口因此都装上, 不会有
        # 「网页版忘了挂」这种半边生效 (05 立过的旗). 注册表一次会话一份, 里面
        # 那条插件的账本按**运行**归零 (挂哪个点由它自己决定, 见 guardrail.install).
        hooks = HookRegistry()
        WriteGuardrail(client=self.client, user_id=buyer_id(context)).install(hooks)
        # 压缩**每会话一份估算器**, 所以在这里造 (理由见 build_compaction_for):
        # 没配就是不压缩 —— 这条会话每一轮把全量历史原样发出去
        compactor, counter = (
            (None, None)
            if self.compaction is None
            else build_compaction_for(self.compaction)
        )
        return ChatSession(
            self.model,
            saver=self.saver,
            tools=tools,
            thread_id=context.thread_id,
            model_name=self.model_name,
            # 出口在这里裹一层脱敏 (由入口显式指名, 见上面的 `redact`): 与护栏、
            # 记录员同一条理由 —— 挂在这唯一一处装配上, 两个入口一起覆盖
            event_sink=redacting_sink(event_sink) if redact else event_sink,
            # 三道刹车一起上: 轮数 / token / 墙钟 —— 只配轮数拦不住"某一轮本身
            # 就很贵"那种跑飞 (见上面两个常量的注释)
            guard=LoopGuard(
                max_turns=self.max_turns,
                max_total_tokens=self.max_total_tokens,
                max_duration_seconds=self.max_duration_seconds,
            ),
            thinking=self.thinking,
            # 业务提示词在业务自己的目录里, 框架目录里不留业务的东西 (PRD §4.8).
            # 名字里带版本, 而版本由清单文件说了算: 读不到清单 (这里) 或读不到那份
            # 文件 (框架) 都是启动期错误, 不会悄悄退回上一版 —— 评估结论要能归因到
            # 具体一版, 静默降级会让跑分张冠李戴.
            prompt_name=f"{PROMPT_NAME}/{resolve_prompt_version()}",
            prompt_dir=PROMPT_DIR,
            hooks=hooks,
            # 上下文压缩 (ticket 18): 账本一字不改, 变的只是**这一次请求发出去的那份
            # 视图** —— 用户看到的记录永不压缩 (见 CONTEXT.md 的「上下文视图」).
            # 两个参数必须成对, 见 build_compaction_for.
            compactor=compactor,
            counter=counter,
            # 记账挂在这一处装配上 (与护栏同一个理由): 两个入口都经过这里, 于是
            # 「网页版记了、命令行没记」这种半边生效不会发生.
            recorder=self._recorder_for(context),
        )

    def _recorder_for(self, context: RunContext) -> ConversationRecorder | None:
        """这个买家的会话记录员 (没配库就是 None = 不记账).

        记录员按 **(租户, 买家)** 绑定: 一个实例只服务一段会话的属主, 写入时不可能
        把别人的账记到自己名下 (见 db/recorder.py 的类 docstring).

        为什么 None 是「不记账」而不是「报错」: 演示环境可能压根没有 Postgres,
        而**没有记录不该让助手不能问答** —— 那是本片最要紧的一条 (写记录失败都
        只降级, 何况没配).
        """
        if self.database is None:
            return None
        return ConversationRecorder(
            database=self.database,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
        )

    async def aclose(self) -> None:
        """进程级收尾: 关掉商城连接池、模型、快照存储、记录库 (谁建谁关).

        为什么**不**改成逐个关会话: 会话与别的会话共用这几件资源 (进程级), 而
        `ChatSession.aclose()` 会把模型与存储一起关掉 —— 关一个会话就顺手把别人
        的也关了. 框架那侧明文写着它从不调它 (`server/sessions.py` 的「谁建谁关」
        一段), 这里同理.
        """
        await self.model.aclose()
        await self.saver.aclose()
        await self.client.aclose()
        if self.database is not None:
            await self.database.dispose()


__all__ = [
    "CONVERSATION_PREFIX",
    "DEFAULT_MAX_DURATION_SECONDS",
    "DEFAULT_MAX_TOTAL_TOKENS",
    "DEFAULT_MAX_TURNS",
    "PROMPT_DIR",
    "PROMPT_MANIFEST",
    "PROMPT_NAME",
    "STARTUP_ERRORS",
    "TENANT_CLI",
    "TENANT_WEB",
    "MinimallService",
    "build_compaction_for",
    "build_context",
    "build_model_for",
    "resolve_prompt_version",
    "thread_id_for",
]
