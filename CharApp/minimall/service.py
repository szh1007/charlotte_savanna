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

from CharAgent.agent import GuardConfigError, LoopConfigError, LoopGuard, RunContext
from CharAgent.checkpoint import CheckpointError, CheckpointSaver
from CharAgent.client import ChatSession, CliOptions, build_model
from CharAgent.model import ModelError
from CharAgent.model.protocol import ChatModel
from CharAgent.prompt import PromptError
from CharAgent.stream import EventSink
from CharAgent.tool import Tool
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.provider import PAYLOAD_USER_ID, MinimallToolProvider

# 会话编号的第一段 (PRD §4.11: `业务:买家ID:对话ID`) —— 快照按它分区, 于是
# 多用户隔离是免费得到的: 换一个买家就是换一个分区, 谁也读不到谁的档.
CONVERSATION_PREFIX = "minimall"

# 一次运行最多几轮模型决策 (LoopGuard) —— 两个入口的默认值同源, 免得一边改了
# 另一边还是老数字. CLI 的 `--max-turns` 缺省值也读它.
DEFAULT_MAX_TURNS = 10

# 业务提示词: 目录 + 名字 + 版本. 盘上的位置是 `{目录}/{名字}/{版本}.prompt`
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
PROMPT_VERSION = "v1"

# 启动期可能抛出的配置类错误 (命令敲错了 / 环境没配好 / 提示词不在), 都不是
# 「运行中出问题」—— 报一句人话就退出, 不打印 traceback. 六者没有共同祖先
# (业务 / 模型 / 快照 / agent / 提示词 各一族), 只能列成元组.
#
# 两个入口共用这一份: CLI 拿它兜住启动装配, server 拿它兜住进程启动 —— 会抛的
# 是同一批错误, 该说的也是同一句人话.
STARTUP_ERRORS: tuple[type[Exception], ...] = (
    MinimallConfigError,
    ModelError,
    CheckpointError,
    LoopConfigError,
    GuardConfigError,
    PromptError,
)


def thread_id_for(user_id: int, conversation_id: str) -> str:
    """会话编号 = `业务:买家ID:对话ID` (PRD §4.11) —— 规则的唯一出处.

    带上买家 ID 之后, 「多用户各看各的」不需要任何额外设计: 快照本来就按会话
    编号分区, 分区键里已经含了身份. 第三段 (对话 ID) 决定「同一买家的哪一段
    对话」—— 换个值就是一段新对话, 历史不串台.
    """
    return f"{CONVERSATION_PREFIX}:{user_id}:{conversation_id}"


def build_context(user_id: int, conversation_id: str) -> RunContext:
    """买家身份 → 运行上下文; **买家身份从哪来, 全项目只有这一处**.

    CLI 从命令行参数取 (`--user-id`), 第二阶段的服务从 Django 转发的请求头取
    (`X-User-Id`) —— 两个入口各自只有一行「从哪取」, 取到之后走的是这里 (PRD
    §4.2). 框架不解释载荷里是什么, 只认 `thread_id`; 而 `provider.provide` 之后
    的一切 (工具、闭包、schema) 与身份来自哪儿完全无关.

    Args:
        user_id: 买家在商城里的 User ID.
        conversation_id: 会话编号的第三段 (同一买家的第几段对话).

    Returns:
        RunContext: 会话编号 + 装着买家身份的载荷 (框架不解释这个载荷).
    """
    return RunContext(
        thread_id=thread_id_for(user_id, conversation_id),
        payload={PAYLOAD_USER_ID: user_id},
    )


def build_model_for(options: CliOptions, writer: Callable[[str], Any]) -> ChatModel:
    """按配置造模型 (借框架的装配: 裸适配器 + 默认套一层重试包装).

    两个入口都从这里拿模型: CLI 的 `writer` 是终端 (重试提示打给用户看), server
    的是日志 (重试提示留给排查).
    """
    return build_model(options, writer)


@dataclass(frozen=True, slots=True)
class MinimallService:
    """客服服务的零件与装配 (一个进程一份, 两个入口共用同一段装配代码).

    attributes:
        client: 商城客户端 (一条连接池 + 一个令牌). **进程级共享** —— 一次会话
            里问十几句、一个进程里几十个买家, 都只用这一条连接池 (见 client.py).
        model: 模型适配器 (含重试包装), 同样进程级共享.
        saver: 快照后端 (按 thread_id 分区, 一个进程一个).
        model_name: 模型名覆盖; None 表示听 .env 的 DEEPSEEK_MODEL_NAME.
        thinking: 思考模式开关; None 表示不传 (上游默认开启). 服务端从
            `CHARAPP_THINKING` 读, 命令行入口从 `--no-thinking` 读 (两边都不填时
            语义相同: 让上游自己决定).
        max_turns: 轮数上限 (防跑飞).

    三个零件都是**进程级**的, 所以谁建谁关: 建它的人在进程退出时调 `aclose()`
    (server 那侧); CLI 只有一次会话, 它沿用既有收尾 (`ChatSession.aclose()` 关的
    正是同一个模型与同一个存储, 再加客户端自己关).
    """

    client: MinimallClient
    model: ChatModel
    saver: CheckpointSaver
    model_name: str | None = None
    thinking: bool | None = None
    max_turns: int = DEFAULT_MAX_TURNS

    async def session_for(
        self, context: RunContext, *, event_sink: EventSink
    ) -> ChatSession:
        """把零件装成一台能问答的机器: 上下文 → 工具 → 会话 (**唯一一处装配**).

        顺序如实反映依赖: 先拿上下文换工具 (提供者是异步的), 再把工具交给会话 ——
        框架的 `ChatSession` 至今不知道 `RunContext` 存在 (见 `agent/provider.py`).

        为什么要收一个 `event_sink`: 事件的出口在**会话构造时**就定死了, 而一个
        会话要连续服务很多次运行. HTTP 那侧由框架按运行分流 (它递进来的是一条
        常驻路由), CLI 那侧是终端渲染 —— 业务只管转交, 不必知道它是什么.

        Args:
            context: 这次运行的上下文 (身份在载荷里).
            event_sink: 事件出口 (框架给的路由或终端的渲染器).

        Returns:
            ChatSession: 装好的会话 (工具 / 提示词 / 模型 / 快照 / 快照分区).

        Raises:
            MinimallConfigError: 载荷里没有买家身份 (装配时忘了放).
        """
        tools: Sequence[Tool] = await MinimallToolProvider(self.client).provide(context)
        return ChatSession(
            self.model,
            saver=self.saver,
            tools=tools,
            thread_id=context.thread_id,
            model_name=self.model_name,
            event_sink=event_sink,
            guard=LoopGuard(max_turns=self.max_turns),
            thinking=self.thinking,
            # 业务提示词在业务自己的目录里, 框架目录里不留业务的东西 (PRD §4.8).
            # 名字里带版本: 读不到就是启动期错误 (PromptNotFoundError), 不会悄悄退回
            # 上一版 —— 评估结论要能归因到具体一版, 静默降级会让跑分张冠李戴.
            prompt_name=f"{PROMPT_NAME}/{PROMPT_VERSION}",
            prompt_dir=PROMPT_DIR,
        )

    async def aclose(self) -> None:
        """进程级收尾: 关掉商城连接池、模型、快照存储 (谁建谁关).

        为什么**不**改成逐个关会话: 会话与别的会话共用这三件资源 (进程级), 而
        `ChatSession.aclose()` 会把模型与存储一起关掉 —— 关一个会话就顺手把别人
        的也关了. 框架那侧明文写着它从不调它 (`server/sessions.py` 的「谁建谁关」
        一段), 这里同理.
        """
        await self.model.aclose()
        await self.saver.aclose()
        await self.client.aclose()


__all__ = [
    "CONVERSATION_PREFIX",
    "DEFAULT_MAX_TURNS",
    "PROMPT_DIR",
    "PROMPT_NAME",
    "PROMPT_VERSION",
    "STARTUP_ERRORS",
    "MinimallService",
    "build_context",
    "build_model_for",
    "thread_id_for",
]
