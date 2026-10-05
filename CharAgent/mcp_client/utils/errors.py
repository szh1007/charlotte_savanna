"""mcp 包的错误族: 配置错 (可照着改) 与 server 侧故障 (要人去看) 分开.

与 `checkpoint` / `tool` / `model` 各包的错误族同一条纪律: **能照着改的**与
**要人去看的**不共用一个类型 —— 前者该在启动期变成一句人话, 后者该带着
上下文进日志. 这里两者都不是 `RuntimeError` 的裸奔: 调用方按类型分派,
不必猜字符串.

- `McpConfigError`: **我们能改的**那一类 —— 名字缺 / 重名 / 前缀非法 / 没 start
  就要工具 / 跨 server 工具重名. **启动期当场报**, 报错里带上是哪个 server 的哪一项.
- `McpServerError`: **改不了、得让那台 server 去改**的那一类 —— 进程起不来 /
  握手失败 / 连接断了 / 游标打转 / 一台之内工具重名 / 工具名不符合模型侧规范.
  与本框架其余部分的故障语义一致: **不吞、不降级** —— 静默少一个工具比报错
  难查得多.
"""

from __future__ import annotations

__all__ = ["McpConfigError", "McpError", "McpServerError"]


class McpError(Exception):
    """mcp 包全部错误的共同祖先 (只在 `except` 想一网打尽时用)."""


class McpConfigError(McpError):
    """配置 / 装配错误: 命令写错、前缀非法、工具重名、没 start 就想拿工具.

    这类错误**一定可以照着报错当场改对** —— 所以消息里必须给出「哪儿错了 +
    该怎么写」, 而不是把问题留给下一层去撞. 反过来, 那些「改了配置也没用」的
    情形 (远端自己坏了) 不出现在这里, 见 `McpServerError`.
    """


class McpServerError(McpError):
    """外部 MCP server 的故障: 起不来 / 握手失败 / 调用打到一条已断的连接.

    它不是我们的 bug, 也不该被降级成「这次没有工具」: 那会让一次配置错的
    server 表现得像「这台 server 本来就没有工具」.
    """
