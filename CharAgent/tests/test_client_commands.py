"""client 交互命令解析测试 (issue 10): `/resume` 那套斜杠命令的翻译规则.

场景 → 断言:
- 四条命令都认得 (resume / history / help / quit)
- 别名等价: exit / q 当 quit, ? / h 当 help
- 宽容写法: 前后空白 / 大小写都不影响结果
- 参数被原样取出 (命令后面那段), 于是「多写了什么」能被指出来
- 普通提问不是命令: 返回 None 且 looks_like_command 为假
- 光一个 `/` 既不是命令也不算「想敲命令」(空前缀, 当作空输入处理)
- 敲错的命令 (如 /resum): 解析返回 None, 但 looks_like_command 为真 ——
  调用方据此报「不认识」而**不**把它发给模型
- 这里**没有**「这句是不是『继续』」的判据: 打断后已完成的工作会收回对话历史
  (见 client/session.py), 于是「继续」就是普通提问, 判断交给模型

被测对象是纯函数 (utils/commands.py), 不碰终端也不碰模型.
"""

from __future__ import annotations

import pytest

from CharAgent.client.utils.commands import (
    COMMAND_PREFIX,
    Command,
    looks_like_command,
    parse_command,
)

# ---------------------------------------------------------------------------
# 四条命令
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/resume", Command.RESUME),
        ("/history", Command.HISTORY),
        ("/help", Command.HELP),
        ("/quit", Command.QUIT),
    ],
)
def test_recognizes_the_four_commands(line: str, expected: Command) -> None:
    """四条命令各自解析成对应的枚举成员, 且不带参数."""
    assert parse_command(line) == (expected, "")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/exit", Command.QUIT),
        ("/q", Command.QUIT),
        ("/?", Command.HELP),
        ("/h", Command.HELP),
    ],
)
def test_aliases_map_to_the_same_command(line: str, expected: Command) -> None:
    """别名指向同一条命令 (少让用户猜, 也少一次「敲错了」的打断)."""
    assert parse_command(line) == (expected, "")


@pytest.mark.parametrize(
    "line",
    ["/RESUME", "  /resume  ", "\t/resume", "/Resume"],
)
def test_whitespace_and_case_are_tolerated(line: str) -> None:
    """前后空白与大小写都不影响解析 (交互场景, 报错不如容错)."""
    assert parse_command(line) == (Command.RESUME, "")


def test_arguments_are_captured_verbatim() -> None:
    """命令后面的原文原样取出 (调用方据此提示「你多写了什么」)."""
    assert parse_command("/resume 3") == (Command.RESUME, "3")
    assert parse_command("/resume   2 3") == (Command.RESUME, "2 3")


# ---------------------------------------------------------------------------
# 不是命令: 提问 / 空输入 / 敲错的命令
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["订单 20260701123456 到哪了", "", "   ", "现在几点?", "exit"],
)
def test_plain_text_is_not_a_command(line: str) -> None:
    """不带 `/` 前缀的行都是提问 (含空行与光秃秃的 exit —— 那是词不是命令)."""
    assert parse_command(line) is None
    assert looks_like_command(line) is False


def test_bare_slash_is_neither_command_nor_a_typo() -> None:
    """光一个 `/`: 不是命令, 也不算「想敲命令」—— 当空输入跳过最不打扰."""
    assert parse_command("/") is None
    assert looks_like_command("/") is False
    assert parse_command("  /  ") is None


def test_unknown_command_looks_like_one_but_does_not_parse() -> None:
    """敲错的命令: 解析给 None, 但 looks_like_command 为真.

    这两个判据配合起来才完整: 只看 None 分不出「提问」与「敲错了」, 前者该发给
    模型, 后者该提示命令表 —— 发出去既费 token 又让人一头雾水.
    """
    assert parse_command("/resum") is None
    assert looks_like_command("/resum") is True
    assert parse_command("/清空") is None
    assert looks_like_command("/清空") is True


def test_prefix_constant_is_a_slash() -> None:
    """命令前缀是 `/` (对外契约: 帮助文本与文档都按它写)."""
    assert COMMAND_PREFIX == "/"
