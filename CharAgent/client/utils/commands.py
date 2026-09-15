"""交互命令解析: 交互模式里那些以 `/` 开头的行 (issue 10).

一句话理解: 交互模式下面, 用户敲的一行要么是「要问 agent 的问题」, 要么是
「给 CLI 自己的指令」—— 用开头的 `/` 区分. 本文件只把一行文本翻译成结论, 一行
动作都不执行 (执行在 app.py 里).

为什么不把命令做得更丰富: 命令越多, 演示时要讲的东西越多. 这四条刚好覆盖
P0 验收要的三件事 —— 中断后接着跑 (resume)、看见快照历史 (history)、看懂怎么用
(help)、退出 (quit). 其余能力 (翻某一帧、time-travel 到指定帧) 属交互式调试台
的范围, 留给 P1/P2 的 server + 前端.

两个纯函数分工明确, 调用方按这个顺序问:
1. `parse_command(line)` -> 是命令就拿到 (命令, 参数); 不是就 None
2. 上一步是 None 时, `looks_like_command(line)` 为真说明用户「想敲命令但敲错了」
   —— 报「不认识这条命令」, 而不是把这行当问题发给模型 (既费 token 又让人
   一头雾水); 为假才是真的在提问

**这里不做「这句是不是『继续』」的判断**: 打断之后已完成的工作已经收回对话历史
(见 client/session.py 的 `_reclaim_progress`), 于是「继续」就是一条普通提问, 模型
看着历史自己接得上 —— 判断交给模型, CLI 只把上下文备齐. 曾经有一版用关键词
启发式去认这类句子, 试下来既多一层误判面、又不如「上下文齐全」来得根本.

纯函数 + 枚举放这里, 是因为它们「无状态、不碰终端也不碰模型」—— 与 checkpoint
的 utils/pending.py 同一类东西 (从已有输入里算出人能直接用的结论), 好单测.
"""

from __future__ import annotations

from enum import StrEnum

# 命令行的前缀 (以它开头的行才当命令看)
COMMAND_PREFIX = "/"


class Command(StrEnum):
    """交互模式支持的四条命令 (值即用户敲的那个词, 不带前缀)."""

    RESUME = "resume"  # 从最新一帧快照接着跑 (Ctrl-C 中断后的续跑)
    HISTORY = "history"  # 打印本会话的快照历史表 (回放调试视图)
    HELP = "help"  # 列出命令与用法
    QUIT = "quit"  # 退出


# 别名: 同一个意思的常见写法都认 (少让用户猜, 也少一次「敲错了」的打断)
_ALIASES: dict[str, Command] = {
    "exit": Command.QUIT,
    "q": Command.QUIT,
    "?": Command.HELP,
    "h": Command.HELP,
}


def parse_command(line: str) -> tuple[Command, str] | None:
    """一行文本 -> (命令, 参数) ; 不是命令就返回 None (那行按提问处理).

    宽容两处写法差异 (交互场景, 报错不如容错): 前后空白 (``" /resume "``) 与
    大小写 (``"/RESUME"``); 另外认几个常见别名 (``exit`` ``q`` 等价于 ``quit``,
    ``?`` ``h`` 等价于 ``help``).

    参数是命令后面剩下的那段原文. 四条命令目前都不吃参数, 保留这一项是为了
    报错时能指出「你多写了 xxx」, 而不是悄悄吞掉.

    Args:
        line: 用户敲的一整行 (不含换行).

    Returns:
        tuple[Command, str] | None: 命令与跟在后面的参数; 不是命令 (不以 ``/``
        开头, 或前缀后面是空的) 或命令名不认识时返回 None.
    """
    text = line.strip()
    if not text.startswith(COMMAND_PREFIX):
        return None
    body = text[len(COMMAND_PREFIX) :].strip()
    if not body:
        return None
    name, _, argument = body.partition(" ")
    word = name.strip().lower()
    if word in _ALIASES:
        return _ALIASES[word], argument.strip()
    try:
        return Command(word), argument.strip()
    except ValueError:
        # 不认识的词: 与「不是命令」走同一条返回值 (None), 由调用方的
        # looks_like_command 再问一次「是敲错了还是真在提问」
        return None


def looks_like_command(line: str) -> bool:
    """这一行看着像在敲命令吗 (以 `/` 开头且后面有内容).

    给调用方用来区分两种「不是命令」: 真人提问 (False) vs 敲错的命令 (True).
    后者要提示「不认识这条命令」并列出可用的, 不能当问题发出去.
    """
    text = line.strip()
    return text.startswith(COMMAND_PREFIX) and len(text) > len(COMMAND_PREFIX)
