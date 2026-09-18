"""命令行入口: `python -m CharApp.minimall.cli` —— 以某个买家的身份跟客服助手对话.

一句话理解: L1a 的那扇门。终端里敲一句「有什么 2000 块以下的手机推荐吗」, 助手
带着 9 个只读工具去打真实的商城接口, 拿真实数据回答; 过程实时打在屏幕上, 多轮
对话连得上, 中途 Ctrl-C 能打断, 说一句「继续」就接着跑。

**为什么先做命令行再做网页** (PRD §4.9): 网页版会同时引入 HTTP 层、流式输出、
Django 转发、前端渲染、跨进程错误传播五个新变量。命令行用最少的变量把
「框架 → 工具 → 商城接口 → 数据库」这条链路先验证一遍, 网页版到时候只加传输层。

这个入口刻意是**薄的** —— 业务真正独有的只有中间那几行, 别的一律复用框架:

| 本文件做的 (业务独有) | 复用框架的 |
|---|---|
| 解析参数 (含「以谁的身份」) | 会话 `ChatSession` (提示词与目录是它的参数) |
| 把参数装成运行上下文 | 模型装配 `build_model` (含重试包装) |
| 装工具 (`provider.provide`) | 快照后端选择 `build_saver_for` |
| 提示符 / 开场白 / 帮助 / 告别, | 交互循环 `InteractiveRepl` + 渲染 `EventPrinter` |
| 四个钩子见 `ServiceRepl` | 结果与打断怎么报 `report_result` / `report_interrupt` |
| | Ctrl-C 打断 `KillSwitch` + 启动杂活 `load_root_env` |

**身份从哪来只有一处** (`build_context`): 现在读命令行参数, 第二阶段换成从
Django 转发的请求头取 (PRD §4.2), 改的就是那一个函数体, 别处一行不动 ——
这也是它被写成独立函数而不是内联进装配的原因。

**交互层为什么是子类而不是抄一份**: 2026-09-19 之前这一页确实抄了框架的交互循环,
抄完当场开始漂 (丢了一句快照帧数提示). 现在接缝落在「措辞」上 —— `ServiceRepl`
只覆盖 `prompt` / `banner` / `help_text` / `farewell`, 分派与执行逻辑仍然只有一份
(见 `CharAgent/client/app.py` 的 `InteractiveRepl`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from CharAgent.agent import (
    GuardConfigError,
    LoopConfigError,
    LoopGuard,
    LoopOutcome,
    RunContext,
)
from CharAgent.checkpoint import BACKEND_NAMES, CheckpointError
from CharAgent.client import (
    ChatSession,
    CliOptions,
    EventPrinter,
    InteractiveRepl,
    KillSwitch,
    build_model,
    build_saver_for,
    load_root_env,
    report_interrupt,
    report_result,
    use_utf8_stdio,
)
from CharAgent.model import ModelError
from CharAgent.model.protocol import ChatModel
from CharAgent.prompt import PromptError
from CharApp.minimall.config import MinimallConfigError, client_from_env
from CharApp.minimall.provider import PAYLOAD_USER_ID, MinimallToolProvider

# 会话编号的第一段 (PRD §4.11: `业务:买家ID:对话ID`) —— 快照按它分区, 于是
# 多用户隔离是免费得到的: 换一个买家就是换一个分区, 谁也读不到谁的档.
CONVERSATION_PREFIX = "minimall"

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
_STARTUP_ERRORS: tuple[type[Exception], ...] = (
    MinimallConfigError,
    ModelError,
    CheckpointError,
    LoopConfigError,
    GuardConfigError,
    PromptError,
)

_EPILOG = """\
两条演示脚本 (L1a 验收要的事):
  1) 问商品:  python -m CharApp.minimall.cli --user-id 3
     试问: 有什么 2000 块以下的商品推荐吗 / 我余额还有多少 / 我最近的订单到哪了
     (示例问句按本机商城现有的商品写的 —— 换成目录里没有的东西也不出错, 助手会
      如实说「没找到」, 那同样是它该有的表现)
  2) 多轮连贯: 上面推荐完之后追问「第二个多少钱」—— 助手接得住上一轮说过的商品
     (模型看得到对话历史, 不需要你重复一遍)

需要商城在跑 (python manage.py runserver), 并在根 .env 里配好内部令牌;
只读: 这个版本不能下单、付款、取消、退款。
"""


# ---------------------------------------------------------------------------
# 启动选项
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinimallCliOptions:
    """一次客服 CLI 运行的启动选项 (解析 argv 的产物, 全程只读).

    attributes:
        user_id: **以哪个买家的身份对话** —— 第一阶段从命令行来, 第二阶段换成
            Django 转发的请求头 (见 `build_context`).
        conversation_id: 会话编号的第三段; 同一买家换一个就是一段新对话 (快照
            按会话编号分区, 于是「两台设备各聊各的」是免费的).
        questions: 非交互模式下依次要问的问题; 空元组表示进交互模式.
        backend: 快照后端 (memory / redis / postgres); None 表示听环境变量.
        model_name: 模型名覆盖; None 表示听 .env 的 DEEPSEEK_MODEL_NAME.
        thinking: 思考模式开关; None 表示不传 (上游默认开启).
        max_turns: 轮数上限 (防跑飞).
        use_retry: 是否给模型套重试包装.
        color: 是否上 ANSI 颜色 (非 TTY / --plain 时关掉).
    """

    user_id: int
    conversation_id: str = "cli"
    questions: tuple[str, ...] = field(default_factory=tuple)
    backend: str | None = None
    model_name: str | None = None
    thinking: bool | None = None
    max_turns: int = 10
    use_retry: bool = True
    color: bool = True

    @property
    def interactive(self) -> bool:
        """是否进交互模式 (没给 --question 就是)."""
        return not self.questions

    @property
    def thread_id(self) -> str:
        """会话编号: `业务:买家ID:对话ID` (PRD §4.11).

        带上买家 ID 之后, 「多用户各看各的」不需要任何额外设计 —— 快照本来就按
        会话编号分区, 分区键里已经含了身份.
        """
        return f"{CONVERSATION_PREFIX}:{self.user_id}:{self.conversation_id}"

    def framework_options(self) -> CliOptions:
        """挑出交给框架的**工厂**的那几个开关 (模型 / 快照 / 颜色).

        为什么借框架的 `CliOptions` 而不是自己再定义一遍: `build_model` 与
        `build_saver_for` 收的就是它 —— 业务这几个字段的语义与框架完全相同,
        各写一份迟早会对不上. 它们只读 `model_name` / `use_retry` / `backend`,
        所以这里只填读得到的; 会话编号与轮数走另一条路 (`build_context` 与
        `build_session` 直接交给 `ChatSession`), 不从这里绕.
        """
        return CliOptions(
            backend=self.backend,
            model_name=self.model_name,
            use_retry=self.use_retry,
            color=self.color,
        )


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器 (帮助文本与选项都是对外契约, 单独一个函数好单测)."""
    parser = argparse.ArgumentParser(
        prog="python -m CharApp.minimall.cli",
        description="minimall 电商客服助手 (命令行版, 只读)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--user-id",
        type=int,
        required=True,
        help="以哪个买家的身份对话 (商城里的 User ID); 第二阶段这条会被 "
        "Django 转发的请求头取代",
    )
    parser.add_argument(
        "--conversation-id",
        default="cli",
        help="会话编号的第三段, 默认 cli; 换一个就是一段新对话 (历史不串台)",
    )
    parser.add_argument(
        "-q",
        "--question",
        action="append",
        metavar="TEXT",
        help="问一句就跑 (可重复给多次); 不给则进交互模式",
    )
    parser.add_argument(
        "--backend",
        choices=BACKEND_NAMES,
        help="快照存储后端; 不给则听 CHARAGENT_CHECKPOINT_BACKEND 环境变量",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help="模型名覆盖 (默认听 .env 的 DEEPSEEK_MODEL_NAME)",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="关闭思考模式 (省 token 也更快; 默认开启)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="一次运行最多几轮模型决策 (LoopGuard), 默认 10",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="不给模型套重试包装 (对照用: 遇到 429 / 5xx 会直接失败)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="不用 ANSI 颜色 (重定向到文件或老终端时用)",
    )
    return parser


def parse_argv(argv: list[str] | None = None) -> MinimallCliOptions:
    """命令行参数 → MinimallCliOptions (唯一一处把 argv 翻译成配置的地方)."""
    args = build_parser().parse_args(argv)
    return MinimallCliOptions(
        user_id=args.user_id,
        conversation_id=args.conversation_id,
        questions=tuple(args.question or ()),
        backend=args.backend,
        model_name=args.model,
        thinking=False if args.no_thinking else None,
        max_turns=args.max_turns,
        use_retry=not args.no_retry,
        # 颜色自动关掉两种情况: 给了 --plain, 或者标准输出不是终端
        color=not args.plain and sys.stdout.isatty(),
    )


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def build_context(options: MinimallCliOptions) -> RunContext:
    """启动参数 → 运行上下文; **买家身份从哪来, 全项目只有这一处**.

    现在是「读命令行参数」. 第二阶段 (网页版) 换成「从 Django 转发的请求头取」
    (PRD §4.2): 那时这个函数多收一个参数 (转发的 user_id), 函数体换掉来源那一
    行, 而 `provider.provide(context)` 之后的一切 —— 工具、闭包、schema ——
    一行不改. 这就是「身份绕开参数表」那条设计能在两个阶段之间原样搬运的原因.

    Args:
        options: 启动选项 (含 --user-id).

    Returns:
        RunContext: 会话编号 + 装着买家身份的载荷 (框架不解释这个载荷).
    """
    return RunContext(
        thread_id=options.thread_id,
        payload={PAYLOAD_USER_ID: options.user_id},
    )


async def build_session(
    options: MinimallCliOptions,
    model: ChatModel,
    client: Any,
    printer: EventPrinter,
) -> ChatSession:
    """把零件装成一台能问答的机器: 上下文 → 工具 → 会话.

    顺序如实反映依赖: 先拿上下文换工具 (提供者是异步的), 再把工具交给会话 ——
    框架的 `ChatSession` 至今不知道 `RunContext` 存在 (见 `agent/provider.py`).

    为什么 `thinking` / `max_turns` 在这里逐个显式传: 它们**不是**模型工厂的参数
    (`build_model` 只管模型名与重试), 而是会话运行时的参数 —— 框架自己的 CLI 也是
    传给 `ChatSession` 的. 漏掉一个的表现是「命令行开关解析了、存下了、但没生效」,
    这种静默失效最难发现 (`thinking` 就是这么漏过一次, 由代码评审抓出来的).
    """
    context = build_context(options)
    tools = await MinimallToolProvider(client).provide(context)
    framework = options.framework_options()
    return ChatSession(
        model,
        saver=build_saver_for(framework),
        tools=tools,
        thread_id=context.thread_id,
        model_name=options.model_name,
        event_sink=printer,
        guard=LoopGuard(max_turns=options.max_turns),
        thinking=options.thinking,
        # 业务提示词在业务自己的目录里, 框架目录里不留业务的东西 (PRD §4.8).
        # 名字里带版本: 读不到就是启动期错误 (PromptNotFoundError), 不会悄悄退回
        # 上一版 —— 评估结论要能归因到具体一版, 静默降级会让跑分张冠李戴.
        prompt_name=f"{PROMPT_NAME}/{PROMPT_VERSION}",
        prompt_dir=PROMPT_DIR,
    )


def build_model_for(
    options: MinimallCliOptions, writer: Callable[[str], Any]
) -> ChatModel:
    """按配置造模型 (借框架的装配: 裸适配器 + 默认套一层重试包装)."""
    return build_model(options.framework_options(), writer)


# ---------------------------------------------------------------------------
# 交互模式
# ---------------------------------------------------------------------------


class ServiceRepl(InteractiveRepl):
    """客服的交互循环: 复用框架那套「读一行 → 分派 → 打印」, 只换说给买家听的话.

    **一行交互逻辑都没有重写** —— 分派顺序 (四条命令 / 其余当提问)、打断处理、结果
    与账目打印、快照历史表全在框架那份里. 下面覆盖的四个钩子就是业务与框架**真正
    不同**的全部: 提示符、开场白、帮助文本、告别语.

    这也正是 2026-09-19 把框架交互层上浮成公共 API 的起因: 业务入口是它的第二个
    调用方, 而在此之前只能把那一页抄一份 —— 抄完立刻开始漂 (丢了一句快照帧数提示).
    上浮之后接缝落在「措辞」这一层, 交互逻辑仍然只有一份.
    """

    prompt = "客服 > "

    def __init__(
        self,
        runner: KillSwitch,
        session: ChatSession,
        options: MinimallCliOptions,
        *,
        reader: Callable[[str], str],
        writer: Callable[[str], Any],
    ) -> None:
        super().__init__(
            runner,
            session,
            options.framework_options(),
            reader=reader,
            writer=writer,
        )
        # 开场白要说清「以谁的身份」与「最多几轮」—— 这两项是业务选项, 框架不认
        self._options = options

    def banner(self) -> str:
        """开场白: 把「现在以谁的身份、跟什么说话、存到哪儿」先摆清楚."""
        capabilities = self._session.saver_capabilities
        traits = [
            "有历史" if capabilities.history else "不留历史",
            "会过期" if capabilities.ttl else "不过期",
        ]
        return "\n".join(
            [
                "",
                "minimall 电商客服 (命令行版, 只读)",
                f"  买家: #{self._options.user_id} · "
                f"会话: {self._session.thread_id} · "
                f"模型: {self._session.model_name}",
                f"  工具: {len(self._session.tool_names)} 个 (商品 / 分类 / 购物车 / "
                f"订单 / 账户) · 轮数上限: {self._options.max_turns}",
                f"  快照: {self._session.saver_name} ({', '.join(traits)})",
                "  输入 /help 看指令; Ctrl-C 打断后说一句「继续」即可接着跑",
                "",
            ]
        )

    def help_text(self) -> str:
        """`/help` 正文: 四条命令与框架那份是同一套, 业务侧没有额外命令."""
        return "\n".join(
            [
                "命令:",
                "  /resume   从最新一帧快照恢复 (想接着跑也可以直接说一句「继续」)",
                "  /history  打印快照历史表 (轮次 / 来源 / 本轮 token 与耗时 / 工具)",
                "  /help     显示这份帮助",
                "  /quit     退出 (/exit 与 /q 也行 —— 命令都要带前导斜杠)",
                "",
            ]
        )

    def farewell(self) -> str:
        """告别语: 这个入口没有 `--resume`, 所以不说那句「--resume 可以接着跑」."""
        return (
            f"\n再见. 会话 {self._session.thread_id} 的存档已留在 "
            f"{self._session.saver_name} 里\n"
        )


# ---------------------------------------------------------------------------
# 进程入口
# ---------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    model: ChatModel | None = None,
    reader: Callable[[str], str] | None = None,
) -> int:
    """CLI 进程入口: 读环境 → 解析参数 → 装配 → 跑 → 收尾释放.

    Args:
        argv: 命令行参数 (None 表示取 sys.argv[1:]).
        model: 模型注入缝 (None 表示按配置造真的) —— 与全仓其他测试同一套做法:
            `ChatModel` 是薄协议, 塞个 MockLLM 进去就能离线跑通整条链路, 被测
            代码一行不改.
        reader: 输入函数 (None 表示 `input`); 测试传一个「按脚本吐行」的可调用
            对象来驱动交互模式.

    Returns:
        int: 进程退出码 —— 0 正常; 1 启动配置错或某次运行失败; 130 被 Ctrl-C
        打断 (沿用 Unix 的 128 + SIGINT 惯例, 便于脚本判分支).

    Raises:
        (不抛: 配置类错误在这里被翻译成一行人话 + 退出码)
    """
    load_root_env()
    use_utf8_stdio()
    options = parse_argv(argv)
    writer: Callable[[str], Any] = print
    printer = EventPrinter(writer=writer, color=options.color)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = KillSwitch(loop)
    client = None
    session: ChatSession | None = None
    try:
        # 连接池一个进程一个: 一次会话里问十几句也只建一次 (见 client.py)
        client = client_from_env()
        session = runner.run(
            build_session(
                options,
                model if model is not None else build_model_for(options, writer),
                client,
                printer,
            )
        )
        return _dispatch(runner, session, options, reader or input, writer)
    except _STARTUP_ERRORS as exc:
        # 配置类错误 (缺令牌 / 缺 API Key / 提示词不在): 报一句人话就退出,
        # traceback 对使用者没有信息量
        writer(f"启动失败: {type(exc).__name__}: {exc}")
        return 1
    except KeyboardInterrupt:
        # 兜底: 交互循环与一次性运行各自接了打断, 这里接的是「启动途中」按下的
        # Ctrl-C —— 统一成 130, 不让 traceback 糊一屏
        writer("")
        writer("已打断")
        return 130
    finally:
        # 收尾释放: 谁建谁关 (模型连接池 + 快照存储 + 商城连接池)
        if session is not None:
            with contextlib.suppress(Exception, KeyboardInterrupt):
                runner.run(session.aclose())
        if client is not None:
            with contextlib.suppress(Exception, KeyboardInterrupt):
                runner.run(client.aclose())
        loop.close()


def _dispatch(
    runner: KillSwitch,
    session: ChatSession,
    options: MinimallCliOptions,
    reader: Callable[[str], str],
    writer: Callable[[str], Any],
) -> int:
    """按选项选一条路走: 一次性跑几步 / 进交互.

    先把「要跑几步」排成一个队列: 某一步失败就不跑后面的 (多半是环境不对,
    再发一次也只是白烧 token). 队列空就进交互模式.
    """
    exit_code = 0
    for question in options.questions:
        exit_code = _run_once(runner, session, writer, question=question)
        if exit_code:
            break

    if options.interactive:
        repl = ServiceRepl(runner, session, options, reader=reader, writer=writer)
        repl_code = repl.run()
        # 交互模式本身恒 0 (那是一次会话, 不是一次运行); 但已经排过的步骤若失败,
        # 那是这次进程的既定事实, 不能被随后的一次聊天盖成 0
        return exit_code or repl_code
    return exit_code


def _run_once(
    runner: KillSwitch,
    session: ChatSession,
    writer: Callable[[str], Any],
    *,
    question: str,
) -> int:
    """非交互地问一句, 打印事件流与结论; 返回退出码 (0 答完 / 1 失败 / 130 打断)."""
    try:
        writer(f"提问: {question}")
        result = runner.run(session.ask(question))
    except KeyboardInterrupt:
        report_interrupt(runner, session, writer)
        return 130
    except (ModelError, CheckpointError) as exc:
        writer(f"[失败] {type(exc).__name__}: {exc}")
        return 1

    report_result(runner, session, result, writer)
    return 0 if result.outcome is LoopOutcome.FINISHED else 1


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
