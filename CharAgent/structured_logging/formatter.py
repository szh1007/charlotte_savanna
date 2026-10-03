"""一事件一行 (difficulties #38): 默认 JSON, 另有给人念的一行.

一句话理解: 日志不再是一句只给眼睛看的话, 而是**一行一个 JSON 对象** —— 机器能
直接解析 (jq / grep / 日志采集器都认), 于是「筛出某次运行的所有行」「按级别挑出来」
这类问题不必先写正则.

**零依赖**: 一个 `json.dumps` 加一层字段整理, 就是下面这几十行. 不为日志引入
structlog / loguru —— 「零框架依赖」是这个项目立身的那句话, 而它的证据就是
`CharAgent/pyproject.toml` 里那八项 (本片之后仍然是八项).

形状 (一个事件一行, 键固定):

    {"ts": "2026-10-03T12:34:56.789+08:00", "level": "WARNING",
     "logger": "charagent.client", "msg": "会话 … 收回进度时读不到身份说明",
     "thread_id": "toy:chat-1", "run_id": null, "request_id": "…",
     "exc": "Traceback (most recent call last):\\n  …"}

| 键 | 从哪来 |
|----|--------|
| `ts` | `record.created` (带时区的 ISO-8601, 毫秒) |
| `level` / `logger` | `levelname` / `name` |
| `msg` | `record.getMessage()` (**已打过码**, 见 filter) |
| 三个 id | 上下文 (没绑就是 `null` —— 键恒在, 形状才固定) |
| `exc` | `record.exc_text` (打过码的那段栈); 没有异常就不出现 |
| 其余键 | 调用方 `extra=` 放的结构化字段 |

**一事件一行是硬保证**: `json.dumps` 把字符串里的换行转义成 `\n`, 于是一段多行
traceback 仍然只占一行 —— 按行消费的那一头 (grep / `tail -f` / 采集器) 不会被它
切开.

**固定键优先**: 调用方 `extra=` 里若带了 `ts` / `level` / `logger` / `msg` / `exc`
同名的键, 会被框架自己那几个盖掉 (骨架键不该被业务数据顶替). 别拿这几个当 extra
的键.

**`ensure_ascii=False`**: 中文照原样写 (日志是给人看的, 转成 `\\u4e2d` 那一串没人
读得下去). 代价是输出流要按 UTF-8 解码 —— 命令行入口已经 `use_utf8_stdio()`,
演示装置也设了 `PYTHONIOENCODING`.

大白话版: 每条日志压成一行 JSON, 该有的键都在, 想看哪个字段就 jq 一下.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from CharAgent.structured_logging.context import ID_FIELDS, current_ids
from CharAgent.structured_logging.record import extras_of

# 框架自己写的固定键 (与上面那张表一一对应). 列出来是给「extra 不许顶掉它们」
# 这句话一个能读的落点, 也是用例按它断言的地方
RESERVED_KEYS: tuple[str, ...] = ("ts", "level", "logger", "msg", "exc")


def _iso_time(created: float) -> str:
    """`record.created` (epoch 秒) → 带时区的 ISO-8601 (毫秒).

    带时区而不是裸的本地时间: 日志被搬去另一台机器看时, 裸时间要靠猜才能对上
    (`2026-10-03T12:34:56` 是哪个时区的?) —— 而带偏移的那一串 <字符串排序即时间
    排序>, 两个格式都省了.
    """
    moment = datetime.fromtimestamp(created).astimezone()
    return moment.isoformat(timespec="milliseconds")


def _payload(record: logging.LogRecord) -> dict[str, Any]:
    """一条记录 → 一个可序列化的字典 (JSON 与纯文本两种格式共用它).

    Note:
        下面这个字面量多一个键, `RESERVED_KEYS` 就要跟着多一个 —— 两处对不上的
        表现是「纯文本那一档把新键当成 extra 打成一坨 JSON 尾巴」, 而 JSON 那一档
        看不出来 (它照写). 之所以不把两边合成一份: 骨架的**值**各有各的算法,
        能合起来的只有名字, 而为了一个名字绕一层并不划算.
    """
    payload: dict[str, Any] = dict(extras_of(record))
    # 固定键**后写**: 它们是这一行的骨架, 不该被同名的 extra 顶掉
    payload.update(
        {
            "ts": _iso_time(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **current_ids().as_dict(),
        }
    )
    if record.exc_text:
        payload["exc"] = record.exc_text
    return payload


def dump_line(payload: dict[str, Any]) -> str:
    """字典 → 一行文本 (`default=str`: 塞进来的东西可能是任何对象).

    为什么兜 `str` 而不是让它抛: 一条日志因为「extra 里有个 datetime」写不出去,
    代价远大于那一格变成字符串 —— 日志是排查的**最后一站**, 它自己不能成为故障点.
    """
    return json.dumps(payload, ensure_ascii=False, default=str)


class JsonFormatter(logging.Formatter):
    """一事件一行 JSON (见模块 docstring 的形状表)."""

    def format(self, record: logging.LogRecord) -> str:
        return dump_line(_payload(record))


class PlainFormatter(logging.Formatter):
    """给人念的一行: 同样的字段, 不套 JSON 的壳 (本地看日志时眼睛舒服些).

    字段一个不少 (三个 id 与 extra 都还在, 只是排成 `key=值` 与一段 JSON 尾巴),
    于是「换个体裁」不会让人在排查时少看到什么 —— `configure_logging(json=False)`
    是**看的方式**不同, 不是**记的内容**不同.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = _payload(record)
        exc = payload.pop("exc", None)
        # 三个 id 与 extra 分开放: 前者是每个进程都有的骨架 (只留绑上的那几个),
        # 后者是这一条自己带的内容
        ids = {key: payload[key] for key in ID_FIELDS if payload.get(key)}
        extras = {
            key: value
            for key, value in payload.items()
            if key not in RESERVED_KEYS and key not in ID_FIELDS
        }
        head = f"{payload['ts']} {payload['level']} {payload['logger']}"
        line = f"{head} {payload['msg']}"
        if ids:
            line += " (" + " ".join(f"{key}={val}" for key, val in ids.items()) + ")"
        if extras:
            line += f" {dump_line(extras)}"
        if exc:
            line += f"\n{exc}"
        return line
