"""CLI 入口: `python -m CharAgent.client` (P0 整体验收线).

一句话理解: 这是「不依赖 server 也能把框架跑起来」的那扇门 —— 用户在终端敲一句
话, agent 带着工具去查、去算, 过程实时打在屏幕上, 中途按 Ctrl-C 能打断, 再敲
`/resume` (或直接说一句「继续」) 就从断点接着跑. P0 阶段的所有零件在这里第一次
被装配成一台真的机器.

为什么它算「验收线」而不是「顺手写个 demo」: 前面八个包的产物一直被测试调用,
但**没有任何生产代码**把它们连起来 (`retry/` 当时生产调用点为零, `AgentLoop(`
只出现在测试与 docstring 里). CLI 是第一个真实调用点: 它一旦跑通, 说明这些协议
(ChatModel / Tool / CheckpointSaver / EventSink) 拼得起来, 而不是各自在自己的
单测里自洽.

三件事在这里各有一处落地:
1. **带工具问答端到端** —— `--question` 或交互模式, 走完整 loop + 七类事件实时打印
2. **重试包装接线** —— `build_model` 里那一行 `RetryingChatModel(chat_model_from_env())`
   就是重试包一直缺的那个接线点; `--no-retry` 可关掉做对照
3. **断点续跑 + 存储切换** —— Ctrl-C 打断 (kill switch) 与 `/resume`; `--backend`
   三选一, loop 与模型一行不动

进程结构 (为什么不是「一次 asyncio.run 跑到底」):
- REPL 是同步的 (要用 `input()`), 而会话是异步的 —— 于是本模块持有一个**常驻
  事件循环**, 每次「跑一件事」用 `KillSwitch.run` 把协程丢进去跑完.
  常驻的理由不只是省事: httpx 的连接池绑定创建它的那个事件循环, 每次
  `asyncio.run` 换一个新循环会让第二次请求踩到「连接属于别的循环」的坑.
- `KillSwitch` 是「Ctrl-C 即时打断」的落脚点: 收到 KeyboardInterrupt 就取消
  正在跑的任务、等它收尾 (该落盘的快照落完), 再把中断抛给上层报给用户 ——
  这正是 difficulties #3 说的 kill switch (`asyncio.Task.cancel`), 只不过
  触发它的是终端信号而不是 HTTP 接口.

这一页里有一部分是**给别的入口用的**, 2026-09-19 才从私有改成公开:
`KillSwitch`(打断)、`InteractiveRepl`(交互循环, 四个钩子见它的 docstring)、
`report_result` / `report_interrupt`(结果与打断怎么报)、`load_root_env` /
`use_utf8_stdio`(进程启动那两件杂活).

改的起因值得记一笔 (它是本仓「扩展点要先被真实调用方撞一次」这条主张的实例):
一个业务侧的命令行入口要复用这一页, 而它们全是私有名 —— 于是那个入口只能把这一页
抄一份; 抄完立刻开始漂 (丢了一句快照帧数提示). 撞出来的结论不是「要不要复用」,
而是**「可复用的位置错了」**: 交互逻辑本来就该是一份, 需要各自的只有**说给用户看
的那几句话**. 上浮因此落在「措辞可覆盖」这一层, 而不是给业务开一个后门.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any, TypeVar

from dotenv import load_dotenv

from CharAgent.agent import (
    GuardConfigError,
    LoopConfigError,
    LoopGuard,
    LoopOutcome,
    LoopResult,
)
from CharAgent.checkpoint import (
    BACKEND_NAMES,
    CheckpointError,
    CheckpointSaver,
    build_saver,
    checkpoint_saver_from_env,
)
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.client.render import (
    EventPrinter,
    format_answer,
    format_result,
)
from CharAgent.client.session import DEMO_TOOLS, ChatSession
from CharAgent.client.utils.commands import (
    Command,
    looks_like_command,
    parse_command,
)
from CharAgent.client.utils.types import DEFAULT_THREAD_ID, CliOptions
from CharAgent.db import PgDatabase
from CharAgent.db.recorder import ConversationRecorder, RunRecorder
from CharAgent.model import ModelError, chat_model_from_env
from CharAgent.model.protocol import ChatModel
from CharAgent.retry import RetryAttempt, RetryCallback, RetryingChatModel, RetryPolicy

# 每次「跑一个协程」返回的结果类型 (只在本模块内用于标注, 故用局部 TypeVar)
_T = TypeVar("_T")

# 仓库根 .env (与本仓其他入口同一位置与同一读法)
# 路径层级: client/app.py -> client/ -> CharAgent/ -> 仓库根
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

# 启动期可能抛出的配置类错误 (都不是「运行中出问题」, 而是「命令敲错了 / 环境
# 没配好」—— 报一句人话就退出, 不打印 traceback). 四者没有共同祖先
# (模型层 / 快照层 / agent 层各一族), 只能列成元组.
_STARTUP_ERRORS: tuple[type[Exception], ...] = (
    ModelError,
    CheckpointError,
    LoopConfigError,
    GuardConfigError,
)

# 交互模式的提示符与欢迎语 (PROMPT 单独拎出来: 测试要按它比对)
PROMPT = "client > "

_EPILOG = """\
演示脚本 (P0 验收线要的三件事):
  1) 带工具问答:   python -m CharAgent.client
     试问: 我的订单 20260701123456 到哪了? 顺便把 3.5 公里换算成英里
  2) 中断续跑:     上面那问跑到屏幕出现 tool_result (工具已执行完) 之后按 Ctrl-C
     打断, 再说一句「继续」—— 已完成的工作已收回对话历史, 那个工具不会重跑
     (/resume 走的是快照恢复那条路: 计数器接续、挂起点补做)
  3) 换快照存储:   python -m CharAgent.client --backend redis
     redis / postgres 需要本机服务在跑; 配 --thread-id 固定会话, 跨进程也能
     用 --resume 接着跑 (内存后端进程一退档就没了 —— 存储介质不同, 语义也不同)
     postgres 顺带把这段会话记进记录表 (会话行 / 运行行 / 消息): 帧的 thread_id
     指向记录层的会话行, 那一行得有人建 (ticket 24) —— 于是这个后端下这段会话
     在库里是完整的, 用 psql 就能复盘
"""


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器 (单独一个函数: 帮助文本与选项都是对外契约, 好单测)."""
    parser = argparse.ArgumentParser(
        prog="python -m CharAgent.client",
        description=(
            "CharAgent 命令行演示: 带工具的问答 + 事件流实时展示 + 断点续跑 "
            "(P0 整体验收线)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
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
        "--thread-id",
        default=DEFAULT_THREAD_ID,
        help=f"会话编号 (快照按它分区), 默认 {DEFAULT_THREAD_ID}",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="启动后先从最新一帧快照接着跑 (没存档就提示一句)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="只打印本会话的快照历史表 (回放调试视图), 不跑模型",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help="模型名覆盖 (默认听 .env 的 DEEPSEEK_MODEL_NAME)",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="关闭思考模式 (省 token 也更快; 默认开启且 reasoning_effort=high)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="一次运行最多几轮模型决策 (LoopGuard), 默认 10",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help="单次输出上限; 设小可以稳定造出 length 截断, 用来看续写路径",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="不给模型套重试包装 (对照演示: 遇到 429 / 5xx 会直接失败)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="不用 ANSI 颜色 (重定向到文件或老终端时用)",
    )
    return parser


def parse_argv(argv: Sequence[str] | None = None) -> CliOptions:
    """命令行参数 -> CliOptions (唯一一处把 argv 翻译成配置的地方).

    颜色自动关掉有两种情况 (都不用用户操心): 给了 `--plain`, 或者标准输出不是
    终端 (重定向进文件 / 管道 / 测试采集) —— 往文件里塞 ANSI 转义符只会让文件
    难读.
    """
    args = build_parser().parse_args(argv)
    return CliOptions(
        backend=args.backend,
        thread_id=args.thread_id,
        questions=tuple(args.question or ()),
        resume=args.resume,
        show_history=args.history,
        model_name=args.model,
        thinking=False if args.no_thinking else None,
        max_turns=args.max_turns,
        max_tokens=args.max_tokens,
        use_retry=not args.no_retry,
        color=not args.plain and sys.stdout.isatty(),
    )


# ---------------------------------------------------------------------------
# 装配 (配置 -> 真零件)
# ---------------------------------------------------------------------------


def build_model(options: CliOptions, writer: Callable[[str], Any]) -> ChatModel:
    """按配置造模型: httpx 裸调适配器 + (默认) 重试包装.

    这是重试包一直在等的接线下手处 —— `retry/` 交付后生产调用点为零, 而重试
    以**组合**方式挂在 ChatModel 协议层 (协议是 Protocol, 不要求继承), 于是
    loop 与 model 一行都不用改: 换个对象传进去就生效.

    重试发生时打一行提示 (on_retry 回调): 演示时能看见「限流了, 等 0.4 秒再试」
    —— 不打印的话, 用户只会觉得这次特别慢. 注意那次失败的响应**已经计费**
    (重试链路的双计费口径), 所以提示里带上原因, 账目不含糊.

    Args:
        options: 启动选项 (模型名 / 是否重试).
        writer: 输出函数 (重试提示往哪儿打).

    Raises:
        ModelConfigError: DEEPSEEK_API_KEY 没配 (报错信息里指向 .env.example).
    """
    base = chat_model_from_env(model=options.model_name)
    if not options.use_retry:
        return base
    return RetryingChatModel(
        base, policy=RetryPolicy(), on_retry=[_retry_notice(writer)]
    )


def _retry_notice(writer: Callable[[str], Any]) -> RetryCallback:
    """造一个「重试了」的通知回调 (打印一行, 不改变重试行为)."""

    def announce(attempt: RetryAttempt) -> None:
        """重试发生时的通知 (on_retry 回调, 只打印不改变重试行为)."""
        writer(
            f"[retry] 第 {attempt.attempt} 次尝试失败, "
            f"{attempt.delay:.1f}s 后重试: {attempt.reason}"
        )

    return announce


def build_saver_for(options: CliOptions) -> CheckpointSaver:
    """按配置挑快照存储: 显式给了 --backend 就用它, 否则听环境变量 (兜底 memory).

    两种来源都指向 checkpoint/config.py 的同一套翻译表 (`build_saver` /
    `checkpoint_saver_from_env`), 本函数只决定「听谁的」.

    Raises:
        CheckpointConfigError: 后端名不认识, 或该后端缺连接信息.
    """
    if options.backend:
        return build_saver(options.backend)
    return checkpoint_saver_from_env()


# CLI 的占位身份 (`tenant_id` / `user_id` 是多租户与属主的概念, 命令行两者都没有)
_CLI_IDENTITY = "cli"


def _recorder_for(saver: CheckpointSaver) -> RunRecorder | None:
    """给这次装配挑记录员: 只有 Postgres 快照后端配一个, 其余后端不配.

    为什么偏偏 Postgres 要配 (ticket 24): 帧的 `thread_id` 指向记录层的
    `charagent_threads`, 写帧之前那一行必须存在 —— 而本 CLI 里没有别的角色会
    建它, 缺了它第一帧就以外键失败告终. 让记录层来建 (它本来就住在这个库里),
    顺带这段会话的账 (运行行 / 消息) 也留了下来. 内存 / Redis 后端的帧不落
    这个库, 也就不需要会话行 —— 那种情况下再挂一个记录员只是多写几张表.

    身份写死 `cli`: 命令行既不区分租户也没有登录用户, 与其让使用者每次多传两个
    参数, 不如如实写「这行是命令行跑出来的」.

    Args:
        saver: 这次装配选定的快照后端.

    Returns:
        RunRecorder | None: 记录员; 非 Postgres 后端给 None (会话照旧不记账).
    """
    if not isinstance(saver, PostgresCheckpointSaver):
        return None
    return ConversationRecorder(
        database=PgDatabase(), tenant_id=_CLI_IDENTITY, user_id=_CLI_IDENTITY
    )


# ---------------------------------------------------------------------------
# 运行: 常驻事件循环 + Ctrl-C 打断
# ---------------------------------------------------------------------------


class KillSwitch:
    """把「跑一个协程」与「Ctrl-C 即时打断」的规矩收在一处 (difficulties #3).

    为什么要它: 交互模式用同步的 `input()` 读输入, 而会话是异步的 —— 需要一个
    地方把协程丢进常驻循环跑完. 顺带把打断语义定死:

    - 收到 `KeyboardInterrupt` (Ctrl-C): 取消正在跑的任务, 再**跑一次循环**等它
      把取消走完 (该落盘的快照落完, 该释放的释放), 然后才把中断抛给上层.
      不等它收尾就退出会留下「任务还没结束但循环已停」的悬空状态.
    - 循环本身不受影响: 打断之后照常能跑下一个协程 —— 这正是「打断后可续跑」
      的前提 (上一轮已落盘的快照还在).

    与 P1 的关系: 取消接口 (POST runs/{id}/cancel) 用同一套 `task.cancel`
    语义, 只是触发源从终端信号换成 HTTP 请求.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def run(self, pending: Coroutine[Any, Any, _T]) -> _T:
        """跑完一个协程; 期间被 Ctrl-C 打断就取消它, 收尾完再把中断抛出去."""
        task = self._loop.create_task(pending)
        try:
            return self._loop.run_until_complete(task)
        except KeyboardInterrupt:
            task.cancel()
            # 取消是「请求」不是「立即生效」: 得再跑一次循环让它真正走完清理路径;
            # 任务被取消时 run_until_complete 抛 CancelledError, 那是预期结果
            with contextlib.suppress(asyncio.CancelledError):
                self._loop.run_until_complete(task)
            raise


# ---------------------------------------------------------------------------
# 共用输出 (交互与一次性两条路走同一份措辞)
#
# 为什么提出来: `-q` / `--resume` 那条路与交互模式都要说「答完了什么」「被打断了
# 存到哪儿了」. 各写一份时已经出现过偏差 (一次性那条漏了存档帧数), 所以措辞只留
# 一处 —— 同一件事两处实现, 迟早走偏.
# ---------------------------------------------------------------------------


def _frame_note(
    runner: KillSwitch, session: ChatSession, *, verbose: bool = False
) -> str:
    """存档情况 (几帧快照 / 后端不留历史) —— 附在结果或打断提示后面.

    Args:
        runner: 常驻循环的跑手 (读快照历史是异步的, 借它同步取一次).
        session: 当前会话.
        verbose: True 时给独立一句 (前缀 `快照: `), False 时给接在账目行尾的
            短句 (` · 快照 ...`) —— 同一个事实在两种位置上的两种排法.

    读失败不上抛: 它只是附注, 不该盖过正文或打断提示.
    """
    prefix = "快照: " if verbose else " · 快照 "
    try:
        count = runner.run(session.frame_count())
    except CheckpointError:
        return ""
    if count is None:
        return f"{prefix}{session.saver_name} 这个后端不留历史"
    return f"{prefix}{session.saver_name} 里 {count} 帧"


def report_result(
    runner: KillSwitch,
    session: ChatSession,
    result: LoopResult,
    writer: Callable[[str], Any],
) -> None:
    """一次运行正常结束: 答复正文 + 一行账目 (附存档情况).

    正文取 `LoopResult.content` 而不是事件流里的 final —— 那是权威值 (截断续写
    时它是跨段拼合结果, 见 render 模块 docstring).
    """
    writer("")
    writer(format_answer(result.content))
    writer("")
    writer(f"[完成] {format_result(result)}{_frame_note(runner, session)}")


def report_interrupt(
    runner: KillSwitch, session: ChatSession, writer: Callable[[str], Any]
) -> None:
    """被打断了: 说清「存到哪儿了」与「怎么接着跑」—— 这是演示的重点.

    **打断** 是 KillSwitch (机制), **取消/中断** 归 Cancellation (对外契约),
    两者刻意分开, 这里不能混用.

    两条接着跑的路都说清, 因为它们的保证不一样: 说一句「继续」走的是**上下文
    接续** (已完成的工作已收回对话历史, 模型自己接上), `/resume` 走的是**快照
    恢复** (计数器接续、挂起点补做, 框架级的保证).
    """
    writer("")
    writer(
        f"[打断] kill switch 已触发, 本次运行停止; "
        f"{_frame_note(runner, session, verbose=True)}"
    )
    writer(
        "[打断] 已完成的工作已收回对话历史: 说一句「继续」就能接着跑 "
        "(工具结果就在历史里, 不会重跑); /resume 则从快照恢复"
    )


def _nothing_to_resume(session: ChatSession) -> str:
    """「这个会话没有可恢复的快照」的提示语 (两条路共用同一份).

    刻意说清两种成因与出路 —— 内存后端在进程退出后不留档, 是「存储介质不同,
    语义也不同」最常在演示里撞上的一次.
    """
    return (
        f"[提示] 会话 {session.thread_id} 没有可恢复的快照 —— 要么这是全新会话, "
        "要么当前后端在进程退出后不留档 "
        "(内存后端就是这样; 换 --backend redis / postgres 才能跨进程接着跑)"
    )


# ---------------------------------------------------------------------------
# 交互循环
# ---------------------------------------------------------------------------


class InteractiveRepl:
    """交互模式的主循环: 读一行 -> 分派 (命令 / 提问) -> 打印结果.

    分派顺序 (与 utils/commands.py 的两个判据配合):
    1. 是认识的四条命令 -> 执行它
    2. 看着像命令但不认识 -> 提示「不认识」, **不**把这行发给模型 (既费 token
       又让人一头雾水)
    3. 其余 -> 当问题问出去

    注意第 3 条里**没有**「这句是『继续』所以走续跑」这类判断: 打断后已完成的工作
    已经收回会话历史 (ChatSession._reclaim_progress), 于是「继续」就是一条普通
    提问, 模型看着历史自己接得上. 判断交回给模型, CLI 只把上下文备齐.

    **业务入口怎么复用这一页** (它原本是私有的, 2026-09-19 上浮): 差异全在「说给
    谁看」的那几句话上, 所以那几句做成了可覆盖的钩子, 分派与执行逻辑一行不用重写:

    | 钩子 | 默认 (框架演示) | 换掉它的场合 |
    |------|----------------|------------|
    | `prompt` (类属性) | `client > ` | 换个提示符, 让人看出现在跟谁说话 |
    | `banner()` | CharAgent CLI 横幅 | 说清「我是哪个业务的助手、以谁的身份」 |
    | `help_text()` | 四条命令的说明 | 命令集不同, 或要补业务侧的说法 |
    | `farewell()` | 「--resume 可以接着跑」 | 入口没有 `--resume` 时别说这句 |

    attributes:
        prompt: 交互提示符 (类属性; 子类覆盖即可).
    """

    # 交互提示符 (类属性而不是构造参数: 它是这一页的「长相」, 不是每次运行的数据)
    prompt: str = PROMPT

    def __init__(
        self,
        runner: KillSwitch,
        session: ChatSession,
        options: CliOptions,
        *,
        reader: Callable[[str], str],
        writer: Callable[[str], Any],
    ) -> None:
        self._runner = runner
        self._session = session
        self._options = options
        self._reader = reader
        self._writer = writer

    def run(self) -> int:
        """循环读输入直到退出; 返回进程退出码 (交互模式恒 0, 除非启动就炸)."""
        self._writer(self.banner())
        while True:
            try:
                line = self._reader(self.prompt)
            except EOFError:
                # Ctrl-Z / 管道读完: 与 /quit 同一条退路
                self._writer("")
                break
            except KeyboardInterrupt:
                # 在提示符处按 Ctrl-C 是「退出」的常规写法, 不当错误处理
                self._writer("")
                break
            text = line.strip()
            if not text:
                continue
            if self._dispatch(text):
                break
        self._writer(self.farewell())
        return 0

    def banner(self) -> str:
        """开场白 (可覆盖): 把「这次在跟什么说话、存到哪儿」先摆清楚.

        演示最怕说不清现在的状态 —— 尤其是换后端时 (内存 / Redis / Postgres
        跑起来一模一样, 差别全在这三行字里).
        """
        capabilities = self._session.saver_capabilities
        traits = [
            "有历史" if capabilities.history else "不留历史",
            "会过期" if capabilities.ttl else "不过期",
        ]
        return "\n".join(
            [
                "",
                "CharAgent CLI (P0 验收演示)",
                f"  模型: {self._session.model_name} · "
                f"工具: {len(self._session.tool_names)} 个 · "
                f"轮数上限: {self._options.max_turns}",
                f"  会话: {self._session.thread_id} · "
                f"快照: {self._session.saver_name} ({', '.join(traits)})",
                "  输入 /help 查看指令集; Ctrl-C 打断 RUN 后说一句「继续」即可续跑",
                "",
            ]
        )

    def help_text(self) -> str:
        """`/help` 的正文 (可覆盖): 命令清单 + 两条最常用的演示指引."""
        return "\n".join(
            [
                "命令:",
                "  /resume   从最新一帧快照恢复 (计数器接续; 想接着跑也可以直接说"
                "一句「继续」)",
                "  /history  打印快照历史表 (轮次 / 来源 / 本轮 token 与耗时 / 工具)",
                "  /help     显示这份帮助",
                "  /quit     退出 (/exit 与 /q 也行 —— 命令都要带前导斜杠)",
                "",
            ]
        )

    def farewell(self) -> str:
        """退出时说的一句 (可覆盖): 存档留在哪儿、怎么接着跑."""
        return (
            f"\n再见. 会话 {self._session.thread_id} 的存档已留在 "
            f"{self._session.saver_name} 里, --resume 可以接着跑\n"
        )

    def _dispatch(self, text: str) -> bool:
        """处理一行输入; 返回 True 表示该退出循环了."""
        parsed = parse_command(text)
        if parsed is not None:
            command, argument = parsed
            if argument:
                # 四条命令都不吃参数: 多写的部分照旧执行, 但要点出来 —— 悄悄
                # 吞掉会让人以为「/resume 3」是在「恢复第 3 帧」
                self._writer(f"/{command} 不吃参数, 你多写了 {argument!r} (已忽略)")
            match command:
                case Command.QUIT:
                    return True
                case Command.HELP:
                    self._writer(self.help_text())
                case Command.HISTORY:
                    self._show_history()
                case Command.RESUME:
                    self._resume()
            return False
        if looks_like_command(text):
            self._writer(f"不认识这条命令: {text} (可用: /help /resume /history /quit)")
            return False
        self._ask(text)
        return False

    # ------------------------------------------------------------------
    # 三个动作
    # ------------------------------------------------------------------

    def _ask(self, question: str) -> None:
        """问一句: 跑 loop 并打印事件流与结论 (被打断则给续跑提示)."""
        try:
            result = self._runner.run(self._session.ask(question))
        except KeyboardInterrupt:
            report_interrupt(self._runner, self._session, self._writer)
            return
        except (ModelError, CheckpointError) as exc:
            # 运行期失败不该带走整个会话: 报一句, 交互继续 (重试耗尽才会到这里)
            self._writer(f"[失败] {type(exc).__name__}: {exc}")
            return
        report_result(self._runner, self._session, result, self._writer)

    def _resume(self) -> None:
        """`/resume`: 从最新一帧快照恢复 (走框架的恢复路径, 不是普通提问)."""
        try:
            result = self._runner.run(self._session.resume())
        except KeyboardInterrupt:
            report_interrupt(self._runner, self._session, self._writer)
            return
        except (ModelError, CheckpointError) as exc:
            self._writer(f"[失败] {type(exc).__name__}: {exc}")
            return
        if result is None:
            self._writer(_nothing_to_resume(self._session))
            return
        report_result(self._runner, self._session, result, self._writer)

    def _show_history(self) -> None:
        """`/history`: 打印本会话的快照历史表 (回放调试视图)."""
        try:
            table = self._runner.run(self._session.history_table())
        except CheckpointError as exc:
            self._writer(f"[失败] 读快照历史失败: {exc}")
            return
        self._writer(table)


# ---------------------------------------------------------------------------
# 进程入口
# ---------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    model: ChatModel | None = None,
    reader: Callable[[str], str] | None = None,
) -> int:
    """CLI 进程入口: 读环境 -> 解析参数 -> 装配 -> 跑 -> 收尾释放.

    Args:
        argv: 命令行参数 (None 表示取 sys.argv[1:]; 测试直接传一个列表).
        model: 模型注入缝 (None 表示按配置造真的) —— 与全仓其他测试同一套做法:
            ChatModel 是薄协议, 塞个 MockLLM 进去就能离线跑通整条链路, 被测代码
            一行不改 (全仓测试共用的注入缝). 真实 API 演示照旧走 None 这条路.
        reader: 输入函数 (None 表示 `input`); 测试传一个「按脚本吐行」的可调用
            对象来驱动交互模式.

    Returns:
        int: 进程退出码 —— 0 正常; 1 启动配置错或某次运行失败; 130 被 Ctrl-C
        打断 (沿用 Unix 的 128 + SIGINT 惯例, 便于脚本判分支). 另有两种由
        argparse 自己给的码: 用法错退 2、`--help` 退 0.

        优先级 (两处容易混, 写死在这里): `-q` / `--resume` 这类一次性步骤的失败
        或打断**优先于**交互模式的 0 —— 已经排过的步骤失败过, 那这次进程的结论
        就是失败, 不该被随后的一次聊天盖成 0; 一次性路径全没排时, 交互模式恒 0
        (那是一次会话, 不是一次运行; 会话里按 Ctrl-C 打断某一次运行不改变进程结论).

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
    session: ChatSession | None = None
    try:
        saver = build_saver_for(options)
        session = ChatSession(
            model if model is not None else build_model(options, writer),
            saver=saver,
            tools=DEMO_TOOLS,
            thread_id=options.thread_id,
            recorder=_recorder_for(saver),
            model_name=options.model_name,
            event_sink=printer,
            guard=LoopGuard(max_turns=options.max_turns),
            max_tokens=options.max_tokens,
            thinking=options.thinking,
        )
        return _dispatch(runner, session, options, reader or input, writer)
    except _STARTUP_ERRORS as exc:
        # 配置类错误 (缺 Key / 后端名不认识 / 参数越界): 报一句人话就退出,
        # traceback 对使用者没有信息量
        writer(f"启动失败: {type(exc).__name__}: {exc}")
        return 1
    except KeyboardInterrupt:
        # 兜底: 交互循环与一次性运行各自接了打断, 这里接的是「启动途中 / 看历史
        # 途中」按下的 Ctrl-C —— 统一成 130, 不让 traceback 糊一屏
        writer("")
        writer("已打断")
        return 130
    finally:
        if session is not None:
            # 收尾释放: 谁建谁关 (模型连接池 + 存储连接). 用户可能在这时又按
            # 一次 Ctrl-C —— 那一刻已经没什么可保的了, 静默收场
            with contextlib.suppress(Exception, KeyboardInterrupt):
                runner.run(session.aclose())
        loop.close()


def _dispatch(
    runner: KillSwitch,
    session: ChatSession,
    options: CliOptions,
    reader: Callable[[str], str],
    writer: Callable[[str], Any],
) -> int:
    """按选项选一条路走: 看历史 / 一次性跑几步 / 进交互.

    顺序有讲究: `--history` 是纯查看 (`--resume` 与 `--question` 在这条路上没有
    意义, 所以与它们同给时直接报错退出 —— 静默丢掉用户敲的问题比报错更难排查);
    其余的先把「要跑几步」排成一个队列 —— `--resume` 排在最前 (先接着上次跑),
    然后是逐个 `-q`. 排成队列而不是写两段循环, 是为了让「某一步失败就不跑后面的」
    这条规矩只有一处: 比如 `--resume` 发现没有存档, 后面的提问就不该再往下走
    (多半是环境不对, 再发一次也只是白烧 token).

    `CliOptions.interactive` 为真 (没给 `-q` 也没给 `--history`) 就进交互模式 ——
    所以单给 `--resume` 是「先续跑一次, 然后接着聊」, 这是有意的组合.
    """
    if options.show_history:
        if options.questions or options.resume:
            writer(
                "--history 只打印存档, 不看问题也不跑模型; 请去掉它, "
                "或去掉 -q / --resume (两者不能同时给)"
            )
            return 1
        try:
            table = runner.run(session.history_table())
        except CheckpointError as exc:
            writer(f"读快照历史失败: {exc}")
            return 1
        writer(table)
        return 0

    steps: list[tuple[bool, str | None]] = []
    if options.resume:
        steps.append((True, None))
    steps.extend((False, question) for question in options.questions)

    exit_code = 0
    for resume, question in steps:
        exit_code = _run_once(runner, session, writer, resume=resume, question=question)
        if exit_code:
            break

    if options.interactive:
        repl = InteractiveRepl(runner, session, options, reader=reader, writer=writer)
        repl_code = repl.run()
        # 交互模式本身恒 0 (那是一次会话, 不是一次运行); 但已经排过的步骤若失败或
        # 被打断, 那是**这次进程**的既定事实, 不能被随后的一次聊天盖成 0
        return exit_code or repl_code
    return exit_code


def _run_once(
    runner: KillSwitch,
    session: ChatSession,
    writer: Callable[[str], Any],
    *,
    resume: bool,
    question: str | None = None,
) -> int:
    """非交互地跑一次 (续跑或提问), 打印事件流与结论; 返回退出码.

    事件流经 `event_sink=printer` 实时打印 —— 与交互模式走的是同一条通道, 不是
    「跑完再回放」. 退出码: 0 表示这次运行正常答完 (outcome=FINISHED), 1 表示
    没答完或抛了错, 130 表示被 Ctrl-C 打断.
    """
    try:
        if resume:
            pending = session.resume()
        else:
            writer(f"提问: {question}")
            pending = session.ask(question or "")
        result = runner.run(pending)
    except KeyboardInterrupt:
        report_interrupt(runner, session, writer)
        return 130
    except (ModelError, CheckpointError) as exc:
        writer(f"[失败] {type(exc).__name__}: {exc}")
        return 1

    if result is None:  # 只有续跑会拿到 None: 没有可恢复的快照
        writer(_nothing_to_resume(session))
        return 1
    report_result(runner, session, result, writer)
    return 0 if result.outcome is LoopOutcome.FINISHED else 1


def load_root_env() -> None:
    """把仓库根 .env 读进环境变量 (读不到就算了, 真环境变量优先).

    为什么这一步必须有: `chat_model_from_env` 与 `checkpoint_saver_from_env` 都
    只读 `os.environ`, 而项目约定是配置写在根 `.env` 里 (不提交, 见根
    .gitignore). 不读它, 用户会拿到「DEEPSEEK_API_KEY 未配置」—— 而他明明配了.

    `override=False`: 已经存在的真环境变量优先, 命令行里 `FOO=bar python -m ...`
    这种临时覆盖照旧生效 (与 tests/conftest.py 的读法一致). 文件不存在时
    python-dotenv 静默返回 —— 缺失的配置会由各自的工厂给出可操作报错, 不必
    在这里先炸一次.
    """
    load_dotenv(_ROOT_ENV, override=False)


def use_utf8_stdio() -> None:
    """标准输入与输出都切成 UTF-8 (errors=replace), 两边各有各的坑.

    **输出侧**: Windows 控制台默认 GBK, 模型答复里的 emoji 或生僻字会让 `print`
    抛 UnicodeEncodeError —— 一次正常的演示不该因为这个中断 (与
    tests/record_llm_samples.py 同一处修法, 那里是录制脚本踩到的).

    **输入侧** (2026-09-15 实测补上): 默认编码同样是 GBK 且 errors=surrogateescape,
    于是**管进来的 UTF-8 中文**会被解成乱码外加一串孤立代理项
    (``'\\udcad'``), 一路带到请求体里, 最后由 httpx 在 ``json.dumps`` 处抛出
    ``UnicodeEncodeError: surrogates not allowed`` —— 报错位置离真正的原因
    (输入编码) 十万八千里. 交互式在 Git Bash 里敲中文同样中招 (pty 发的是 UTF-8
    字节). 切成 UTF-8 + errors=replace 之后, 认不出的字节退化成替换字符而不是
    代理项, 至少还能把这句话问出去.

    Windows 控制台那一路也安全: 控制台走的底层 API 本来是 UTF-16, 这里的编码只
    是中间那层往返, 换成 UTF-8 只会更不容易丢字符 (GBK 连 emoji 都编不回去).
    """
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


__all__ = [
    "InteractiveRepl",
    "KillSwitch",
    "build_model",
    "build_parser",
    "build_saver_for",
    "load_root_env",
    "main",
    "parse_argv",
    "report_interrupt",
    "report_result",
    "use_utf8_stdio",
]
