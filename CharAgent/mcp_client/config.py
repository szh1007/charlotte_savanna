"""MCP 消费侧的配置: 「连哪几台 server、怎么起」翻成一份只读的规格表.

一句话理解: 本模块只认**数据** —— 一份 JSON 形状的配置进, 一串
`McpServerSpec` 出; 不去连任何东西 (那是 `client.py` 的事), 也不读环境变量
(业务侧读 env 是业务的事, 框架不碰).

**认的形状是既有的那个约定**, 不是我们发明的: Claude Desktop / Claude Code 的
`mcpServers` 那一层 ——::

    {"mcpServers": {
        "demo": {"command": "python", "args": ["-m", "demo_server"]},
        "fs":   {"command": "npx", "tool_prefix": "fs_",
                 "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    }}

于是同一份配置能直接从别人家的文档里抄过来, 也能反过来贴回去. 我们只读其中五个
键 (`command` / `args` / `env` / `tool_prefix` / `call_timeout`), **认不得的键
一律跳过**: 那份 JSON 还带着别家的字段 (`type` / `disabled` / `alwaysAllow`…),
认不得就报错等于把「抄一份配置」变成一件要手工删字段的事.

两个刻意的取舍:

1. **`tool_prefix` 是我们加的, 不是标准字段** —— 多 server 场景下两台 server
   都叫 `search` 是常态, 前缀是让它们共存的那条路 (默认不加: 名字按远端的原样
   交给模型, 撞了当场报, 见 `provider.py` 的仲裁).
2. **`call_timeout` 也不在标准里**, 而它落在框架自己的那道超时闸上 (`Tool.
   timeout`, #15): 超时那套语义 (取消协程 / 结果未知 / 中断本次运行) 已经设计过
   一遍, MCP 工具没有理由再自造一套.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from CharAgent.mcp_client.utils.errors import McpConfigError

# 配置里装 server 表的那一层 (Claude Desktop / Claude Code 的既有约定)
SERVER_TABLE_KEY = "mcpServers"

# server 名: 只用来做报错里的指代与给人看, 但**不许带空白** —— 一个带空格的名字
# 拼进错误消息里会读不清, 也可能被顺手拿去当前缀 (那一层的规矩见 TOOL_PREFIX_PATTERN).
#
# 判形状一律用 `.fullmatch` 而不是 `.match` + `$`: `$` 在**尾随换行**之前也成立
# (2026-10-05 实测: 名字后面跟一个换行能过), 而那个换行会一路带进工具名或环境里.
# 这条坑对任何拿正则判名字的地方都成立 (代码评审抓的).
_SERVER_NAME_PATTERN = re.compile(r"\S{1,64}")

# 前缀: 它会被拼在远端工具名前面交给我们这边的模型, 所以与 `@tool` 那道工具名
# 规矩取同一个字符集 (字母 / 数字 / 下划线 / 连字符); 允许空串 = 不加前缀
TOOL_PREFIX_PATTERN = re.compile(r"[a-zA-Z0-9_-]{0,64}")


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """一台 MCP server 的启动规格 (纯数据; 不含任何连接状态).

    attributes:
        name: 配置里那个名字 —— 只用来说清「是哪一台」(报错 / 日志 / 仲裁).
        command: 可执行文件名或路径 (按 PATH 找, 与 subprocess 同一套).
        args: 传给它的参数.
        env: **叠加**在这个进程的安全默认环境之上的额外变量 (SDK 1.29 实测:
            `{**安全默认, **env}`) —— 不是「只留这几项」. 需要给 server 传
            凭据时走这里.
        cwd: 子进程的工作目录, None = 随客户端进程. **不是可有可无的一项**:
            以 `python -m 某个包` 起的 server 靠 cwd 才找得到那个包 (子进程的
            `sys.path[0]` 就是 cwd), 客户端从别的目录起它时, 少了这一项会变成
            `ModuleNotFoundError` —— 报在客户端界面上是一句「server 起不来」.
        tool_prefix: 拼在远端工具名前面的前缀, 默认空 (见模块 docstring 第 1 条).
        call_timeout: 单次调用的超时秒数, None = 取框架缺省 (与 `Tool.timeout`
            同一个语义, 见模块 docstring 第 2 条).

    Raises:
        McpConfigError: 名字 / 命令 / 前缀 / 超时任何一个不合法 —— 构造期就报,
            别等连的时候才发现起不来.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    cwd: str | None = None
    tool_prefix: str = ""
    call_timeout: float | None = None

    def __post_init__(self) -> None:
        """构造期校验 (配置错的地方只有这一处; 之后的每一层都可以照直用)."""
        if not _SERVER_NAME_PATTERN.fullmatch(self.name):
            raise McpConfigError(
                f"server 名 {self.name!r} 不合法: 不能为空、不能带空白, 最长 64 字符"
            )
        if not self.command.strip():
            raise McpConfigError(
                f"server {self.name!r} 的 command 是空的 —— 写可执行文件名或路径"
            )
        if not TOOL_PREFIX_PATTERN.fullmatch(self.tool_prefix):
            raise McpConfigError(
                f"server {self.name!r} 的 tool_prefix {self.tool_prefix!r} 不合法: "
                f"只能由字母 / 数字 / 下划线 / 连字符组成"
            )
        timeout = self.call_timeout
        if timeout is None:
            return
        # 与工具层 `_validate_timeout` 同一条规矩: 必须是正数秒 —— 0 与负数会让
        # 超时闸当场开火, 字符串则连开火都到不了 (那是另一个错, 报出来看不懂)
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise McpConfigError(
                f"server {self.name!r} 的 call_timeout 应为秒数 (int / float), "
                f"实际: {timeout!r}"
            )
        if timeout <= 0:
            raise McpConfigError(
                f"server {self.name!r} 的 call_timeout 必须 > 0 秒, 实际: {timeout}"
            )

    @property
    def prefix(self) -> str:
        """工具名前缀 (没有就是空串 —— 调用方不必判 None)."""
        return self.tool_prefix


def parse_server_specs(config: Mapping[str, Any]) -> tuple[McpServerSpec, ...]:
    """配置 (JSON 那一层) → 一串规格; 每一处毛病都指名道姓地报.

    Args:
        config: 整份配置 (`{"mcpServers": {...}}`), 或**已经剥掉外层**的那张表
            (`{"名字": {...}}`) —— 两种都收: 前者是从别人家文档里抄下来的原样,
            后者是程序里手写的那一份.

    Returns:
        tuple[McpServerSpec, ...]: 按配置里的书写顺序 (Python 的 dict 保序),
        于是「哪台先连」是确定的.

    Raises:
        McpConfigError: 表不是映射 / 一个 server 都没有 / 某一台的字段类型不对.
            消息里带上是**哪一台的哪个字段**, 使用者的手上只有那份 JSON.
    """
    if not isinstance(config, Mapping):
        # 报**传进来的那个东西**的类型, 不是我们兜出来的 None —— 实测过一次:
        # 早先那句 `... if isinstance(...) else None` 会把 list / str / int 全
        # 说成 NoneType, 使用者照着一句假线索去找, 越找越远
        raise McpConfigError(
            f"MCP 配置应是一个映射 ({{名字: {{command, args…}}}}), 实际: "
            f"{type(config).__name__}"
        )
    table = config.get(SERVER_TABLE_KEY, config)
    if not isinstance(table, Mapping):
        raise McpConfigError(
            f"MCP 配置应是一个映射 ({{名字: {{command, args…}}}}), 实际: "
            f"{type(table).__name__}"
        )
    if not table:
        raise McpConfigError(
            f"MCP 配置里一个 server 都没有 ({SERVER_TABLE_KEY} 那张表是空的) —— "
            f"要么别配, 要么把 command 写对"
        )
    return tuple(_spec_of(name, entry) for name, entry in table.items())


def _spec_of(name: Any, entry: Any) -> McpServerSpec:
    """一个 server 的表项 → 规格 (逐字段取值 + 判类型)."""
    if not isinstance(name, str):
        raise McpConfigError(f"MCP 配置里的 server 名应是字符串, 实际: {name!r}")
    if not isinstance(entry, Mapping):
        raise McpConfigError(
            f"server {name!r} 的表项应是一个映射 (command / args…), 实际: "
            f"{type(entry).__name__}"
        )
    return McpServerSpec(
        name=name,
        command=_text_field(name, entry, "command", required=True),
        args=_args_field(name, entry),
        env=_env_field(name, entry),
        cwd=_text_field(name, entry, "cwd") or None,
        tool_prefix=_text_field(name, entry, "tool_prefix"),
        call_timeout=_timeout_field(name, entry),
    )


def _text_field(
    name: str, entry: Mapping[str, Any], field: str, *, required: bool = False
) -> str:
    """读一个字符串字段 (缺了给空串; `required` 的那个缺了当场报)."""
    value = entry.get(field)
    if value is None:
        if required:
            raise McpConfigError(
                f"server {name!r} 缺 {field!r} —— 起 server 必须知道起什么"
            )
        return ""
    if not isinstance(value, str):
        raise McpConfigError(
            f"server {name!r} 的 {field} 应是字符串, 实际: {type(value).__name__}"
        )
    return value


def _args_field(name: str, entry: Mapping[str, Any]) -> tuple[str, ...]:
    """读 args: 一串字符串 (缺了给空元组; 里面混了非字符串当场报)."""
    value = entry.get("args")
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise McpConfigError(
            f"server {name!r} 的 args 应是一串字符串 (JSON 数组), 实际: "
            f"{type(value).__name__}"
        )
    for item in value:
        if not isinstance(item, str):
            raise McpConfigError(
                f"server {name!r} 的 args 里有非字符串项: {item!r} —— args 是要"
                f"原样交给子进程的参数, 不做类型转换"
            )
    return tuple(value)


def _env_field(name: str, entry: Mapping[str, Any]) -> Mapping[str, str] | None:
    """读 env: 字符串到字符串的映射 (缺了给 None = 不动默认环境)."""
    value = entry.get("env")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise McpConfigError(
            f"server {name!r} 的 env 应是一个映射 (变量名: 值), 实际: "
            f"{type(value).__name__}"
        )
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise McpConfigError(
                f"server {name!r} 的 env 里有非字符串项: {key!r}: {item!r} —— "
                f"环境变量只能是字符串 (数字也要加引号)"
            )
    return dict(value)


def _timeout_field(name: str, entry: Mapping[str, Any]) -> float | None:
    """读 call_timeout (缺了给 None = 取框架缺省; 值域校验在 spec 的构造期)."""
    value = entry.get("call_timeout")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise McpConfigError(
            f"server {name!r} 的 call_timeout 应是秒数 (数字), 实际: "
            f"{type(value).__name__}"
        )
    return float(value)


__all__ = [
    "SERVER_TABLE_KEY",
    "TOOL_PREFIX_PATTERN",
    "McpServerSpec",
    "parse_server_specs",
]
