"""日志出口的测试支撑: 装一块内存缓冲 / 事后把配置还原 (difficulties #38).

一句话理解: `configure_logging` 改的是**全局状态** (根 logger 的 handler 与级别,
外加三家吵闹库的级别), 而用例跑在同一个进程里 —— 不还原的话, 后面跑的用例会莫名其妙
地多几行输出 (或者少几行), 而那种串味最难查. 这里给出两个上下文管理器把这件事做干净.

| 名字 | 干什么 |
|------|--------|
| `logging_to` | 把出口临时接到一块缓冲 / 一个文件上, 出块还原 |
| `restore_logging` | 块里随便动日志配置 (比如调一次 `main()`, 它自己会配), 出块还原 |

**为什么它住在包里而不是某一个 `tests/` 下** (与 `db/testing.py` 同一个理由): 有两个
消费方, 分属两个项目 —— 框架侧 (`CharAgent/tests/`) 与业务侧 (`CharApp/tests/`).
在本仓里 `CharAgent/tests/xxx` 是可 import 的 (靠命名空间包, 那边的用例一直在这么用),
但**装出来的包里没有它** (`pyproject.toml` 的 `namespaces = false` 与 `include =
["CharAgent*"]`: 有 `__init__.py` 的才是包, 而 `tests/` 没有). 于是跨项目要共用的
支撑得住在**随包走**的地方 —— 两边用同一份, 不会漂.

**为什么不进门面** (它不在本包 `__init__.py` 的 `__all__` 里): 它是测试支撑, 不是库
API —— 与 `FakeRecordDatabase` 同一条规矩, 需要的人按完整路径取
(`from CharAgent.structured_logging.testing import logging_to`).

**它不 import pytest**: 这是随包走的模块, 而 pytest 是开发依赖 (那八项里没有它).
两个上下文管理器都只用标准库, 于是「装了这个包的人也能用」成立.

大白话版: 用例里想接住日志就 `with logging_to(buf, redactor=...)`, 想事后收拾干净
就 `with restore_logging():` —— 用完把世界还成原样.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import IO

from CharAgent.redact.protocol import Redactor
from CharAgent.structured_logging.config import _NOISY_LOGGERS, configure_logging


@contextmanager
def restore_logging() -> Generator[None]:
    """块内随便动日志配置, 出块时**还原成进去时的样子**.

    还原三样: 根 logger 的 handler 列表与级别, 以及那三家吵闹库的级别
    (`configure_logging` 会把它们压到 WARNING —— 不还原的话, 后面某个想验 httpx
    行为了的用例会看到一个改过的值, 而那是另一个用例留下的).
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_noisy = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in saved_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(saved_level)
        for name, level in saved_noisy.items():
            logging.getLogger(name).setLevel(level)


@contextmanager
def logging_to(
    stream: IO[str], *, redactor: Redactor, json: bool = True
) -> Generator[None]:
    """块内的日志写进 `stream`, 出块还原 (见 `restore_logging`).

    Args:
        stream: 接日志的地方 (内存缓冲 / 一个打开的文件).
        redactor: 打码员 —— 与生产那一次调用给**同一个**, 用例验的才是真打码.
        json: 与 `configure_logging` 的同名参数一致.
    """
    with restore_logging():
        configure_logging(
            level=logging.INFO, redactor=redactor, json=json, stream=stream
        )
        yield
