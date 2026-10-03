"""运行级服务台账: 这一趟跑下来是哪个模型在服务 (difficulties #14 的记账那一半).

**它解决的问题**: 熔断切换发生在**一次运行的中途** —— 主模型熔断后, 这一趟的
后半段是备份模型答的. 而运行行的 `model` 列 (与按它查的价目表) 在此之前只有一个
值, 于是「实际是谁在服务」与「记的是谁」会分家: 主模型挂了、备份答的话, 却按主
模型的单价算钱 —— 那正是 #13 那条「不给假账」要防的事.

**为什么不能只是包一层对象**: 模型对象是**进程级共享**的 (CLI 一个进程一次装配,
server 一个进程一份), 而台账是**一次运行一份** —— 记在模型对象上, 第二句问话
会读到第一句的痕迹 (server 并发时更糟: 两个请求的痕迹互相串). 于是台账挂在一个
`contextvars` 变量上: 它按 asyncio 任务隔离 (一次 HTTP 请求 / 一次命令行提问就是
一个任务), 由运行的拥有者 (会话) 在开跑前开一段作用域.

**谁来写**: `FailoverChatModel` 每次拿到**成功的响应**时记一笔 (只有真答了话的
模型才算服务过 —— 失败的尝试产出 0 个 token, 不该出现在账上).

**谁来读**: 会话把结果交给记录员的那一刻 (见 `client/session.py`) —— 读出来的是
一个模型名, 或「两个都用过」这个事实 (组合名), 见 `ServingRecord.model_name`.

**没开作用域时**: 一笔都不记 (台账是 None), 读的人拿到自己给的默认名 —— 于是
「没用 failover 包装」与「用了但一次都没切」在账上长得一样, 都是配置里那个模型名.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from CharAgent.model.utils.config import MODEL_NAME_SEPARATOR


class ServingRecord:
    """一次运行里「谁服务过」的台账 (按首次出场排序, 同名只记一次).

    不是线程安全对象, 也不该跨运行复用 —— 一次运行造一个 (`serving_scope()`
    会替你造), 跑完就没用了.
    """

    __slots__ = ("_names",)

    def __init__(self) -> None:
        self._names: list[str] = []

    def note(self, name: str) -> None:
        """记下「这个模型服务过一次」(重复记同一个名字是幂等的)."""
        if name not in self._names:
            self._names.append(name)

    @property
    def names(self) -> tuple[str, ...]:
        """这一趟服务过的模型名 (按首次出场排序); 空元组 = 一次模型调用都没有."""
        return tuple(self._names)

    def model_name(self, default: str | None = None) -> str | None:
        """这一趟该记成哪个模型名 (记账用).

        - 一个模型都没服务过 (还没调 / 调用全失败): 给 `default` —— 配置里那个
          名字是**跑之前就定下的事实**, 不该因为「这一次没答上话」而丢;
        - 只有一个: 就是它 (熔断切过去了的话, 这里记的是**实际在服务**的那个);
        - 两个都用过: 组合名 (`主+备`), 价目表里查不到 —— 于是金额留空并写明原因,
          而不是按其中某一个的单价算一笔对不上的账.

        Args:
            default: 没有可记的名字时用的那个 (通常是配置里配的模型名).

        Returns:
            str | None: 落库的模型名.
        """
        if not self._names:
            return default
        if len(self._names) == 1:
            return self._names[0]
        # 两个都用过: 拼成组合名 (分隔符与「为什么拼而不是算」见 model/utils/config.py)
        return MODEL_NAME_SEPARATOR.join(self._names)


_current: ContextVar[ServingRecord | None] = ContextVar(
    "charagent_serving_record", default=None
)


@contextmanager
def serving_scope(record: ServingRecord | None = None) -> Generator[ServingRecord]:
    """开一段「这一段运行谁在服务」的作用域; 出块时把上一段还回去.

    对齐 `structured_logging.log_context` 的做法 (contextvars + token 还原):
    一段运行嵌一段运行 (比如续跑) 时, 内层出块后外层读到的仍是自己那一份.

    Args:
        record: 用哪本台账; None 表示现造一本 (常见用法: `with serving_scope() as r`).

    Yields:
        ServingRecord: 这一段的台账 (模型层往里记, 会话出块前读它).
    """
    bound = record if record is not None else ServingRecord()
    token = _current.set(bound)
    try:
        yield bound
    finally:
        _current.reset(token)


def note_serving(name: str) -> None:
    """往当前作用域的台账里记一笔 (没开作用域 = 什么也不做).

    模型层唯一的写入口 —— 它不该知道「谁在读、读了干什么」.
    """
    record = _current.get()
    if record is not None:
        record.note(name)


def current_serving() -> ServingRecord | None:
    """当前作用域的台账; 没开作用域返回 None (读的人据此回退到默认名)."""
    return _current.get()
