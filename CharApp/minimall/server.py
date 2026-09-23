"""服务进程: `python -m CharApp.minimall.server` —— 客服助手的 HTTP 入口.

一句话理解: 把命令行那套客服搬到 HTTP 上. Django 转发一次请求过来 (带内部令牌
与买家 ID), 服务把这次问答的**流式事件**推回去. 框架 (`CharAgent/server/`) 提供
HTTP + SSE 那一层, 业务只接两个插座: 认证解析 (认下这次请求是谁的) 与装配 (把
零件装成一台能问答的机器) —— 后者直接复用 `service.py` 那一处, 不抄第二份.

请求进来之后发生什么 (每一步谁负责)::

    POST /runs {"message": "我余额还有多少"}
      ├─ 1. 认证: X-Internal-Token 对不对 (fail closed)
      ├─ 2. 取身份: X-User-Id → 买家 ID (缺了 / 不是整数 → 明确拒绝)
      ├─ 3. 拼上下文: build_context(买家, 对话)   <- service.py 那一处
      ├─ 4. 装配会话: 这个会话编号第一次出现时调一次 (之后复用, 历史连得上)
      └─ 5. 跑 + 推 SSE (框架负责)

用户按「停止」时走的是框架的另一个端点 (`POST /runs/{id}/cancel`): 认证 / 取身份 /
拼对话走的是**同一个插座** (`MinimallContexts`) —— 所以「只能取消自己那段会话里的
运行」不需要业务这边写一行 (判据是会话编号里含买家, 见 `service.thread_id_for`).

**推给浏览器的事件是脱敏后的** (`redaction.py`, ADR-0003): 工具的参数原文与返回正文
换成一句中文短语, 它们不再出门 (模型自己在答复或思维链里复述的值不在此列 —— 那是
模型的话, 见 `redaction.py` 开头那段范围说明). 开关在 `MinimallSessions.provide`
那一处给出 (`redact=True`) —— 框架把事件出口交给业务的那一行, 也是浏览器这条路唯一
的出口 (命令行那条路给 False: 它的出口是开发者自己的终端). 装配处 (service.py) 收的
是**必填参数**, 于是「某个入口忘了说自己的出口是不是浏览器」不存在默认值可兜.

**身份与令牌**: 两个头都是 Django 转发来的 —— 浏览器 ↔ Django 是唯一真正验证
「你是谁」的地方, 这里只是同一信任域内的转发 (PRD §4.10 的三层信任模型; 为什么
敢信裸的 `X-User-Id` 见 `CharApp/docs/adr/0001-...`). 所以本进程不查库、不认
cookie, 只认一个令牌 + 一个买家 ID.

**头是 ASCII**: HTTP 头按字节传, 发送侧 (httpx / Django 的转发) 按 ASCII 编码,
Starlette 读出来是按 latin-1 解码的 str —— 中文令牌 / 中文买家 ID 在这一层走不通
(框架的 server 用例踩过一次). 令牌与买家 ID 本来就该是 ASCII, 这条不是问题;
但别把别的东西 (比如会话标题) 往头里塞 —— 那种值放请求体.

**身份从哪来**: 本文件里只有 `buyer_id_from_request` 与 `conversation_id_from_request`
两行「从头里取」, 取到之后走的是 `service.build_context` —— 与命令行入口同一个
函数 (PRD §4.2 那句「只换身份从哪取这一小段」兑现的地方).

**收尾**: 进程级的三件资源 (商城连接池 / 模型 / 快照) 由本模块建、本模块关 (谁的
进程谁负责). 会话归框架管、按 thread_id 长驻, 与别的会话**共用**这三件资源 ——
所以这里不关会话: `ChatSession.aclose()` 会把模型与存储一起关掉, 关一个会话等于
顺手关了别人的 (框架的 `server/sessions.py` 明文写着它从不调它).

**不做的事**: 不写面向用户的文案 (框架只转发事实 —— 错误码 + message, 降级话术
归 Django 那侧, 见框架 `docs/DESIGN.md`); 不做前端 (issue 06).

**与记录表那条线的关系** (ticket 17 起): 本进程建一个 `PgDatabase` 交给装配, 于是
每轮问答的账写进 `charagent_threads` / `runs` / `messages`, 而框架据此把 `/history`
指向记录表、并多注册几条会话路由 (`GET /conversations` 列表与搜索, 以及 ticket 20
的 `POST /conversations/title|pin|delete` 三个管理动作). 业务这一侧**一行 SQL 都不
写** —— 它只是把库入口递过去 (写什么、怎么分层全在框架的 `db/` 里).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI
from starlette.requests import Request

from CharAgent.agent import RunContext
from CharAgent.client import (
    ChatSession,
    CliOptions,
    build_saver_for,
    load_root_env,
    use_utf8_stdio,
)
from CharAgent.db import PgDatabase
from CharAgent.server import ServerAuthError, create_app
from CharAgent.stream import EventSink
from CharApp.minimall.client import HEADER_TOKEN, HEADER_USER_ID
from CharApp.minimall.config import (
    ServerConfig,
    client_from_env,
    context_config_from_env,
    server_config_from_env,
    thinking_from_env,
)
from CharApp.minimall.service import (
    STARTUP_ERRORS,
    TENANT_WEB,
    MinimallService,
    build_context,
    build_model_for,
    resolve_prompt_version,
)

logger = logging.getLogger(__name__)

# 会话编号第三段的头 (缺省时服务端自己兜一个).
#
# 为什么它可以缺: `thread_id = 业务:买家ID:对话ID` 里前两段决定「是谁」, 这一段
# 只决定「同一买家的哪一段对话」—— 不给就是同一个买家算一段, 多轮照样连得上
# (验收里「同一 thread_id 连问两句」走的就是这条路). Django 侧的客服页面会给每个
# 标签页发一个 (issue 06), 于是「两台设备各聊各的」在服务端不需要多写一行.
#
# 另外两个头的名字 (令牌 / 身份) 在 `client.py` —— 那两个名字是**打商城时也用的
# 同一个头** (同一信任域里同一套约定), 所以由那边定义; 这一个只属于本服务 (打商城
# 不带它), 因此留在这里.
HEADER_CONVERSATION_ID = "X-Conversation-Id"

# 没带上面那个头时用的第三段 (Django 转发层是当前唯一的调用方)
DEFAULT_CONVERSATION_ID = "web"


# ---------------------------------------------------------------------------
# 插座一: 认证 + 解析 (HTTP 请求 → 运行上下文)
# ---------------------------------------------------------------------------


def buyer_id_from_request(request: Request) -> int:
    """从请求头取买家 ID —— **身份从哪来, server 侧就是这一行**.

    Raises:
        ServerAuthError: 头缺失 / 空 / 不是整数. 这里**明确拒绝**, 而不是让它变成
            一次「查不到数据」: 身份是装配期的输入 (PRD §4.2 那条链路的第一环),
            它缺了就该当场说「你的请求不完整」, 而不是伪装成「商城没有这笔数据」.
            状态码取 401 (认不出这次运行是谁的), 错误码单列一个, 便于转发方
            (Django) 分辨「自己忘了转发身份」与「令牌不对」.
    """
    raw = (request.headers.get(HEADER_USER_ID) or "").strip()
    if not raw:
        raise ServerAuthError(
            f"请求头缺少 {HEADER_USER_ID}: 身份必须由转发方给出 (PRD §4.2), "
            f"本层不猜、也不从请求体里认",
            code="invalid_identity",
        )
    try:
        return int(raw)
    except ValueError as exc:
        raise ServerAuthError(
            f"请求头 {HEADER_USER_ID} 不是整数: {raw!r}", code="invalid_identity"
        ) from exc


def conversation_id_from_request(request: Request) -> str:
    """从请求头取对话编号 (会话编号的第三段); 没带就用默认那一段.

    Note:
        值的合法性 (非空 / 不超长 / 无空白) 由框架在装配会话时校验
        (`checkpoint` 的标识符规则). 本层不重复那条规则 —— 生成规则该由**发号
        的一方**保证 (Django 侧的页面, issue 06), 这里只负责原样透传.
    """
    value = (request.headers.get(HEADER_CONVERSATION_ID) or "").strip()
    return value or DEFAULT_CONVERSATION_ID


@dataclass(frozen=True, slots=True)
class MinimallContexts:
    """插座一 (认证 + 解析): 请求 → 运行上下文 —— 框架的 `ContextProvider`.

    形状是有个 `provide` 就算, 不继承基类 (与 L1a 的 `MinimallToolProvider` 同款).

    Args:
        token: 期望的内部令牌 (与商城侧同值). 空串 = 没配 = 一律拒绝 (fail closed,
            与商城侧 `IsInternalService` 同一条: 不给「忘了配就默认放行」留口子).
    """

    # repr=False: 令牌是共享秘密, 不该跟着对象被打印进 traceback 或日志
    token: str = field(repr=False)

    async def provide(self, request: Request) -> RunContext:
        """认下这次请求: 先认证, 再取身份 (顺序有意: 不认识的人不该看见任何业务信息)."""
        self._require_token(request)
        # ↓ 身份从哪来: 就这几点, 余下全是共用的 build_context
        return build_context(
            buyer_id_from_request(request),
            conversation_id_from_request(request),
            # 这个入口 = 网页端 (命令行那个入口自己传 TENANT_CLI): 同一买家在两条
            # 来路上的会话因此分开放, 左栏只显示网页里聊过的 (见 service.TENANT_CLI)
            tenant_id=TENANT_WEB,
        )

    def _require_token(self, request: Request) -> None:
        """校验令牌 (常量时间比较); 对不上一律 401, 且不说细节.

        Raises:
            ServerAuthError: 令牌缺失 / 不对 / 服务端压根没配.

        Note:
            这里**不** strip 头值 (身份那个头会 strip): 令牌要么逐字节相同, 要么
            就是另一个令牌 —— 带空格的「几乎对」不该算对. 配置侧的空格由
            `token_from_env` 负责去掉 (那是配置书写问题, 不是 wire 问题).
        """
        provided = request.headers.get(HEADER_TOKEN, "")
        expected = self.token.encode("utf-8")
        # 比较前先编成字节: `str` 版的 compare_digest 只吃 ASCII, 碰到非 ASCII 会
        # 抛 TypeError (变成 500). 头值由 Starlette 按 latin-1 从字节解出来, 所以
        # 编回去用 latin-1; 编不动 (理论上不可能) 就当对不上.
        try:
            provided_bytes = provided.encode("latin-1")
        except UnicodeEncodeError:
            raise ServerAuthError() from None
        if not expected or not secrets.compare_digest(provided_bytes, expected):
            # 默认消息与默认码都不区分「没带令牌」与「令牌错」: 想区分的是日志,
            # 不是响应 (与商城侧同一个口径).
            raise ServerAuthError()


# ---------------------------------------------------------------------------
# 插座二: 装配 (运行上下文 → 会话)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinimallSessions:
    """插座二 (装配): 上下文 → 会话 —— 框架的 `SessionProvider`.

    这里**没有装配逻辑**, 只有一行转交: 装配在 `service.MinimallService.session_for`,
    与命令行入口共用同一份. 框架只在某个会话编号第一次出现时调本方法, 之后一直
    复用同一个会话 —— 所以对话历史连得上 (框架的 `server/sessions.py` 负责登记).

    Args:
        service: 进程级的零件与装配 (见 `service.py`).
    """

    service: MinimallService

    async def provide(
        self, context: RunContext, *, event_sink: EventSink
    ) -> ChatSession:
        """装配这个会话 (并宣告: 这个出口是**浏览器**, 要脱敏).

        为什么「要不要脱敏」由本入口声明 (而不是装配处自己猜): 威胁模型是「谁能
        看到**浏览器**」(ADR-0003), 而 `EventSink` 只是一个协议 —— 框架递进来的
        常驻路由与 CLI 的终端渲染器在类型上长得一样, 猜不出来. 本方法正是框架把
        sink 交给业务的那一处, 也是浏览器唯一的那条出口, 所以只有这里说得清.

        (`redact=` 是**必填**参数: 命令行那条路给 False —— 它的出口是开发者自己的
        终端, 按框架的载荷契约打 `name(args)` / `name ok: summary`, 包上脱敏反而
        会把那两行变成 `add_to_cart( (畸形 JSON))`.)
        """
        return await self.service.session_for(
            context, event_sink=event_sink, redact=True
        )


# ---------------------------------------------------------------------------
# 装配与进程入口
# ---------------------------------------------------------------------------


def build_service(writer: Callable[[str], Any]) -> MinimallService:
    """建这个进程要用的三件资源: 商城客户端 + 模型 + 快照后端.

    为什么是**进程级**: 一个进程只该有一条到商城的连接池、一个模型适配器 (含重试
    包装)、一个快照后端 —— 每个请求新建一遍会把连接池建没 (命令行那侧同一个
    道理, 只是它一个进程只服务一个买家). 会话与它们的关系见模块 docstring 的「收尾」.

    Args:
        writer: 重试提示往哪儿写 (服务进程里是日志 —— 没有终端可以打给用户看).

    Returns:
        MinimallService: 装配好的零件 (调用方负责在进程退出时 `aclose()`).

    Raises:
        MinimallConfigError: 内部令牌没配 (进程启动就该说清, 而不是等第一个请求),
            或思考模式开关写了看不懂的值.
        ModelError: 模型 API Key 没配.
        CheckpointConfigError: 快照后端名不认识 / 缺连接信息.
    """
    # 提示词清单在**进程启动期**先读一遍: 会话装配 (session_for) 那一次才是真正
    # 用它的时候, 但那时已经是第一个买家在等了 —— 清单写坏该让进程起不来, 而不是
    # 让每个买家撞一句"启动失败". 与上面那几项同一个性质 (配置对不上的话, 现在说).
    resolve_prompt_version()
    # 服务端没有命令行开关: 模型名 / 快照后端 / 是否重试都听 .env (CliOptions 的
    # 缺省语义就是「听环境变量」); color 是终端的事, 服务端一律关掉.
    # 思考模式不在 CliOptions 里 (它是会话运行时的参数), 所以单独从 CHARAPP_THINKING
    # 读 —— 不填 = 不传该参数, 走上游默认 (开启).
    framework = CliOptions(color=False)
    return MinimallService(
        client=client_from_env(),
        model=build_model_for(framework, writer),
        saver=build_saver_for(framework),
        thinking=thinking_from_env(),
        # 记录表那条线: 连接配置与快照后端同源 (根 .env 的 CHARAGENT_DB_DSN /
        # PGSQL_*). **构造不连库** (PgDatabase 的引擎是懒建的), 于是没配库 /
        # 库不在线都不拦住进程启动 —— 真到写记录时连不上就降级 (日志 + 提示行,
        # 见 db/recorder.py), 买家的问答不受影响.
        # 那句「不受影响」只覆盖记录线: 快照后端是 Postgres 时, 帧的 thread_id
        # 还要求会话行存在 (ticket 24), 而那一行由记录员建 —— 两者共用同一个库,
        # 所以「库连不上」对帧同样是拦路虎 (降级的是记账, 不是落帧).
        database=PgDatabase(),
        # 上下文压缩的五个旋钮 (CHARAPP_CONTEXT_*, ticket 18): 不填全走默认值,
        # 于是"没配"与"配了默认那套"是同一回事 —— 但**装配处拿到的一定是一份具体
        # 的配置**, 不是 None (None 是"不压缩"那条路, 只有用例会给).
        # 与上面几项同一个性质: 读坏了 (比如水位线写成 1.5) 就该让进程起不来,
        # 而不是等某个买家聊长了才发现 (MinimallConfigError 在 STARTUP_ERRORS 里).
        compaction=context_config_from_env(),
    )


def create_minimall_app(service: MinimallService, config: ServerConfig) -> FastAPI:
    """装配客服服务应用: 框架的 `create_app` + 业务的两个插座.

    怎么跑 (uvicorn / 测试里的 ASGI transport / 别的) 由调用方决定 —— 与框架同一条
    纪律: 这里只交出 app, 不起服务.

    Args:
        service: 进程级零件与装配.
        config: 监听地址 / 端口 / 校验令牌.

    Returns:
        FastAPI: 装好的应用 —— 框架占那七条路 (问一句 / 停一次 / 读历史 / 列会话,
        外加改名 / 置顶 / 删除三个会话管理动作), 后六条业务这边一行不用写; 其中
        要记录库的那四条 (列会话 + 三个动作) 只在给了库时才注册, 没给就是 404.
        启动日志那行报的是**全量七条**, 不为 `database=None` 那种装配分叉 ——
        它只出现在用例里 (生产路径 `build_service` 一定给库), 为一句话加个分支不值.

    Note:
        取消端点**不需要业务这边多写一行**: 它复用同一个 `MinimallContexts` 认人
        (见 `CharAgent/server/app.py` 的取消契约), 本函数交出去的还是那两个插座.
    """
    return create_app(
        context_provider=MinimallContexts(token=config.token),
        session_provider=MinimallSessions(service=service),
        # 把记录表那条线一并交给框架: 它据此把 /history 指向记录表, 并多注册一条
        # GET /conversations (前端左侧列表要的). 没配库时这里是 None, 那两条路
        # 各自退回「没有记录层」的样子 (见 create_app 的说明).
        database=service.database,
    )


async def _serve(app: FastAPI, service: MinimallService, config: ServerConfig) -> None:
    """跑服务, 并在**同一个事件循环**里收尾.

    为什么不用 `uvicorn.run(app, ...)` 一行了事: 进程级的资源是在这个进程里建的,
    收尾也该在同一个循环里做 —— 另起一个循环去关 httpx 连接池, 关的是一批「属于
    另一个循环」的连接. 于是这里自己持有循环: 起服务、等它结束、关资源, 三件事
    按顺序发生.

    Note:
        Ctrl-C 停机时 uvicorn 收完尾会**把信号重新抛出来** (它的 `capture_signals`
        如此设计), 所以这里看到的不是一条干净返回 —— 由 `main` 接住.
    """
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.host, port=config.port, log_level="info")
    )
    try:
        await server.serve()
    finally:
        # 关不掉也别让收尾的异常盖住真正的停机原因 (比如那一下 Ctrl-C)
        with contextlib.suppress(Exception):
            await service.aclose()


def _configure_logging() -> None:
    """让本进程自己的日志看得见 (启动的一句人话 / 重试提示 / 停机).

    为什么不直接 `basicConfig(level=INFO)`: 那会把 httpx 每个请求一行的 INFO
    也接到控制台 —— 一次问答十几次工具调用, 有用那几行会被冲走. 这里只给自己
    这个包的 logger 挂一个输出口, 其余照旧 (uvicorn 自己那套照常输出).
    """
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # 别再往根上冒: 使用者可能自己配了 root handler, 那会打两遍
    logger.propagate = False


def main() -> int:
    """进程入口: 读环境 → 建零件 → 起服务 → 退出时收尾.

    Returns:
        int: 0 正常退出 (Ctrl-C 停机也算正常); 1 启动配置错.

    Raises:
        (不抛: 配置类错误在这里被翻译成一行日志 + 退出码; 端口被占用由 uvicorn
        自己报错退出)
    """
    _configure_logging()
    load_root_env()
    use_utf8_stdio()
    try:
        config = server_config_from_env()
        service = build_service(writer=logger.info)
    except STARTUP_ERRORS as exc:
        # 配置类错误 (令牌没配 / API Key 没配 / 快照后端不认识): 报一句人话就退出,
        # traceback 对开机的人没有信息量 —— 与命令行入口同一条规矩, 同一份清单.
        logger.error("启动失败: %s: %s", type(exc).__name__, exc)
        return 1

    logger.info(
        "客服服务启动中: http://%s:%d (七条路: 问一句 / 停一次 / 读历史 / 列会话 / "
        "改名 / 置顶 / 删除)",
        config.host,
        config.port,
    )
    try:
        asyncio.run(_serve(create_minimall_app(service, config), service, config))
    except KeyboardInterrupt:
        # Ctrl-C 是这个服务的正常停机方式 (见 _serve 的 Note), 不是故障
        logger.info("已停机")
    return 0


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
