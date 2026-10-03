"""三个 id 的携带: 一次运行 / 一次请求的标志, 用 contextvars 而不是 threading.local.

一句话理解: 日志要能回答「这一行属于哪次运行」, 而打日志的地方 (loop / 工具 /
记录员 / 重试 / 各个收尾分支) 手上都没有那几个编号 —— 于是把编号挂在**上下文**上,
谁打日志谁抬头自己取, 不必从函数签名一路传下去.

| id | 谁绑 | 它是什么 |
|----|------|---------|
| `thread_id` | 会话层 (`ChatSession.ask` / `resume`) | 会话编号 (快照按它分区) |
| `run_id` | 同上 (编号定下来之后补绑) | **记录层**那一行的编号 |
| `request_id` | server 层 (`X-Request-Id` 头, 没有就生成) | 一次 HTTP 请求 |

**为什么是 `contextvars` 而不是 `threading.local`** (这一条最容易做错): 框架是
asyncio 的 —— 一个事件循环上跑着几十个任务, 它们在**同一个线程**里交替执行.
`threading.local` 按线程隔离, 于是 A 请求绑的号会被 B 请求读到 (两者根本不在同一个
线程上跑) —— 那种串号不会报错, 只会让日志说假话, 而说假话的日志比没有日志更糟.
`contextvars` 按**任务**隔离 (`asyncio.create_task` 会复制一份当前上下文), 同时
`asyncio.to_thread` 把这份上下文一起带进线程池里的同步代码 —— 两层都覆盖到了
(快照的 Postgres 实现正是走 `to_thread` 的那条路).

对照 (本片要避免的形态): `project/rag_text2sql/main.py` 把 `request_id` 写死成一个
常量, 于是整条 HTTP 链路上的所有请求是同一个 id, 「链路追踪」是空的.

**三个字段恒在**: 序列化出来的 JSON 里这三个键**总是**出现 (没绑就是 `null`) ——
形状固定的日志才好筛 (`jq 'select(.run_id == "…")'` 在键缺失时直接报错). 值为空是
诚实的: 命令行那条路没有 HTTP, 也就没有 `request_id`.

大白话版: 给「这次运行」发三个号牌, 打日志的人不必被层层传参, 抬头就能看见.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from CharAgent.structured_logging.utils.errors import LoggingConfigError

# 三个 id 的字段名 (顺序即它们出现在日志里的顺序). 这一条是**唯一**的名单:
# 绑定时按它校验, 取用时按它遍历 —— 两处各抄一份的话, 加了第四个号时会漏一处
ID_FIELDS: tuple[str, ...] = ("thread_id", "run_id", "request_id")

# 一个号一个 ContextVar (而不是共用一个 dict): 各自有各自的栈, `bind` 一个不会
# 动到另外两个, 出块时的还原也就各归各的
_VARS: dict[str, contextvars.ContextVar[str | None]] = {
    name: contextvars.ContextVar(f"charagent_{name}", default=None)
    for name in ID_FIELDS
}


@dataclass(frozen=True, slots=True)
class TraceIds:
    """一次运行 / 一次请求的三个编号 (没绑的那几个是 None).

    attributes:
        thread_id: 会话编号 (快照按它分区).
        run_id: **记录层**那一行运行的编号 —— 拿它去
            `python -m CharAgent.client.trace <run_id>` 能回看这次运行 (账 / 工具
            调用 / 视图都在). 它**不是** server 层的 `new_run_id()`: 那个只活在
            进程里, 管的是「这一条 SSE 流」, 与快照和账单无关 (见 `server/runs.py`).
        request_id: 一次 HTTP 请求的编号 (从 `X-Request-Id` 头来, 没有就生成).
            **它与 `charagent_runs.request_id` 不是一回事** (同名不同义, 别互相填):
            库里那一列是**幂等键** (#17: 客户端重试时带同一个值, 服务端发现已经跑过就
            返回已有那一行, 不重跑), 而这里是一次网络请求的号. 两者的语义正好相反 ——
            幂等键要**故意复用**, 请求号天然**每次不同**.
    """

    thread_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """三个字段的字典 (格式化时直接并进 JSON)."""
        return {name: getattr(self, name) for name in ID_FIELDS}


def current_ids() -> TraceIds:
    """当前上下文里绑着的三个编号 (没绑的是 None)."""
    return TraceIds(**{name: _VARS[name].get() for name in ID_FIELDS})


class TraceBinding:
    """一次 `log_context` 里已绑上的号牌 (可以中途补绑).

    为什么要有「中途补绑」这一手: `run_id` 是**开跑之后**才有的 —— `ChatSession.ask`
    先绑 `thread_id` (装配时就定了), 而运行编号要等 `_begin_run` 把那一行建出来才
    拿得到. 于是上下文管理器交出的不是一个死的快照, 而是一个还能补绑的凭据.

    同一个名字绑第二次 = **换一个值** (先还回去再绑), 于是「同一个号在栈上留两笔」
    不会发生 —— 那会让出块时的还原只还掉最外面那层, 里面那层永远留着.

    Attributes:
        (无公开属性; 绑过什么只有 `close` 关心)
    """

    __slots__ = ("_tokens",)

    def __init__(self) -> None:
        self._tokens: dict[str, contextvars.Token[str | None]] = {}

    def bind(self, **ids: str | None) -> None:
        """绑一个 / 几个号牌 (值为 None 的跳过 —— 「没有这个号」不是「清空它」).

        Raises:
            LoggingConfigError: 名字不在 `ID_FIELDS` 里 (见 `_var_of`).
        """
        for name, value in ids.items():
            var = _var_of(name)
            if value is None:
                continue
            self._unbind(name)
            self._tokens[name] = var.set(value)

    def close(self) -> None:
        """把绑过的号牌**全部还回去** (反序: 同一个变量的栈是后进先出)."""
        for name in reversed(list(self._tokens)):
            self._unbind(name)

    def _unbind(self, name: str) -> None:
        token = self._tokens.pop(name, None)
        if token is not None:
            _VARS[name].reset(token)


@contextmanager
def log_context(**ids: str | None) -> Generator[TraceBinding]:
    """在块内绑上这几个号牌, 出块时**还回去**.

    **出块必须还**: 不还的话这个号会一直挂在这条任务上 —— 同一个连接上下一次请求
    打的日志会带着上一次的 `request_id`, 命令行那条路则是「第二句问话短暂带着第一
    句的运行号」. 两种都不报错, 只是说假话.

    Args:
        **ids: `thread_id` / `run_id` / `request_id` 三选几.

    Yields:
        TraceBinding: 还能中途补绑的凭据 (见它的说明).

    Raises:
        LoggingConfigError: 名字不认识 (比如 `thread` 少写了 `_id`).
    """
    binding = TraceBinding()
    binding.bind(**ids)
    try:
        yield binding
    finally:
        binding.close()


def _var_of(name: str) -> contextvars.ContextVar[str | None]:
    """按名字取那一个 ContextVar.

    Raises:
        LoggingConfigError: 名字不在 `ID_FIELDS` 里 —— 打错一个字的表现是**这个号
            根本没绑上**, 而日志照写, 于是「三个字段」里少一个而不报错.
    """
    var = _VARS.get(name)
    if var is None:
        known = ", ".join(ID_FIELDS)
        raise LoggingConfigError(f"不认识的 id 名: {name!r} (认识的是: {known})")
    return var
