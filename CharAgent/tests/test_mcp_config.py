"""MCP 消费侧的配置解析: 认哪份形状 / 哪里错了要当场报.

这一页全是**纯函数** (不连任何东西), 所以断的是两件事:

1. **认别人家的形状** —— 那份 `{"mcpServers": {...}}` 是 Claude Desktop /
   Claude Code 的既有约定, 抄过来就能用; 别家的字段 (`type` / `disabled`…)
   认不得就跳过, 不是错.
2. **错的地方指名道姓** —— 报错消息要带上「哪一台的哪个字段」, 因为使用者手上
   只有那份 JSON. 用例连消息里的关键词一起断 (只断异常类型的话, 一句
   "配置错了" 也能过, 而那句话对使用者毫无用处).
"""

from __future__ import annotations

from typing import Any

import pytest

from CharAgent.mcp_client import McpConfigError, McpServerSpec, parse_server_specs

# ---------------------------------------------------------------------------
# 认得的那份形状
# ---------------------------------------------------------------------------


def test_parses_the_claude_desktop_shape() -> None:
    """整份配置 (带 `mcpServers` 那层) 直接能读 —— 从别人家文档里抄的就是它."""
    specs = parse_server_specs(
        {
            "mcpServers": {
                "demo": {"command": "python", "args": ["-m", "demo_server"]},
            }
        }
    )

    assert len(specs) == 1
    spec = specs[0]
    assert (spec.name, spec.command, spec.args) == (
        "demo",
        "python",
        ("-m", "demo_server"),
    )
    assert (spec.env, spec.tool_prefix, spec.call_timeout) == (None, "", None)


def test_accepts_the_inner_table_without_the_wrapper() -> None:
    """已经剥掉外层的那张表也认 (程序里手写配置时不必自己套一层)."""
    specs = parse_server_specs({"demo": {"command": "python"}})

    assert [spec.name for spec in specs] == ["demo"]


def test_keeps_the_written_order() -> None:
    """顺序即连接顺序 —— 它决定了工具交出去的顺序, 所以要是确定的."""
    specs = parse_server_specs(
        {
            "zeta": {"command": "z"},
            "alpha": {"command": "a"},
            "mu": {"command": "m"},
        }
    )

    assert [spec.name for spec in specs] == ["zeta", "alpha", "mu"]


def test_reads_cwd_as_well() -> None:
    """`cwd` 也读 (它在标准里, 而且是 `python -m ...` 那种起法的必需品)."""
    [spec] = parse_server_specs(
        {"demo": {"command": "python", "cwd": "D:/somewhere", "args": ["-m", "x"]}}
    )

    assert spec.cwd == "D:/somewhere"
    # 没给就是 None (不是空串): SDK 那边 None 表示"随客户端进程"
    assert parse_server_specs({"demo": {"command": "python"}})[0].cwd is None


def test_reads_the_five_keys_we_support() -> None:
    """五个键 (args / env / tool_prefix / call_timeout 三选 + command) 逐个读出来."""
    [spec] = parse_server_specs(
        {
            "demo": {
                "command": "npx",
                "args": ["-y", "some-server"],
                "env": {"API_KEY": "k"},
                "tool_prefix": "fs_",
                "call_timeout": 12.5,
            }
        }
    )

    assert spec.args == ("-y", "some-server")
    assert spec.env == {"API_KEY": "k"}
    assert spec.prefix == "fs_"
    assert spec.call_timeout == 12.5


def test_unknown_keys_are_skipped_not_rejected() -> None:
    """别人家的字段 (type / disabled / alwaysAllow…) 认不得就跳过.

    抄一份配置不该变成「手工删字段」的活儿 —— 那些键不是我们的, 也不影响我们
    怎么起这台 server.
    """
    [spec] = parse_server_specs(
        {
            "demo": {
                "type": "stdio",
                "command": "python",
                "disabled": False,
                "alwaysAllow": ["search"],
            }
        }
    )

    assert spec.command == "python"


# ---------------------------------------------------------------------------
# 错的地方当场报 (消息里带上「哪一台的哪个字段」)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "expected", "names_the_server"),
    [
        # 前两条针对的是**整份配置**, 说不到具体哪一台 —— 所以不要求带 server 名
        # 报的必须是**传进来那个东西**的真实类型 (早先会一律说成 NoneType)
        pytest.param([], "list", False, id="整份配置是列表"),
        pytest.param(7, "int", False, id="整份配置是数字"),
        pytest.param({"mcpServers": {}}, "一个 server 都没有", False, id="一台都没有"),
        # 以下每一条都是**某一台**的问题: 消息里必须出现它的名字
        pytest.param({"demo": "python"}, "映射", True, id="表项不是映射"),
        pytest.param({"demo": {"args": []}}, "command", True, id="缺 command"),
        pytest.param({"demo": {"command": 7}}, "字符串", True, id="command 不是字符串"),
        pytest.param(
            {"demo": {"command": "x", "args": "y"}}, "args", True, id="args 是字符串"
        ),
        pytest.param(
            {"demo": {"command": "x", "args": ["a", 3]}},
            "非字符串",
            True,
            id="args 里有非字符串项",
        ),
        pytest.param(
            {"demo": {"command": "x", "env": ["A=1"]}}, "env", True, id="env 不是映射"
        ),
        pytest.param(
            {"demo": {"command": "x", "env": {"A": 1}}},
            "非字符串",
            True,
            id="env 的值不是字符串",
        ),
        pytest.param(
            {"demo": {"command": "x", "call_timeout": "10"}},
            "秒数",
            True,
            id="call_timeout 是字符串",
        ),
    ],
)
def test_a_bad_entry_says_which_server_and_which_field(
    config: Any, expected: str, names_the_server: bool
) -> None:
    """每一处毛病都要说清是哪一台的哪个字段 —— 使用者手上只有那份 JSON."""
    with pytest.raises(McpConfigError) as caught:
        parse_server_specs(config)

    message = str(caught.value)
    assert expected in message
    if names_the_server:
        assert "'demo'" in message, f"消息里没说是哪一台: {message}"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({"name": ""}, "server 名", id="名字是空的"),
        pytest.param({"name": "有 空格"}, "server 名", id="名字里有空白"),
        pytest.param({"command": "   "}, "command", id="command 是空白"),
        pytest.param({"tool_prefix": "fs."}, "tool_prefix", id="前缀里有非法字符"),
        pytest.param({"call_timeout": 0}, "必须 > 0", id="超时是 0"),
        pytest.param({"call_timeout": -1.0}, "必须 > 0", id="超时是负数"),
        pytest.param({"call_timeout": True}, "秒数", id="超时是布尔"),
    ],
)
def test_a_spec_validates_itself(overrides: dict[str, Any], expected: str) -> None:
    """直接构造 `McpServerSpec` 也走同一道校验 (不必先过配置解析那一层)."""
    fields: dict[str, Any] = {"name": "demo", "command": "python"}
    fields.update(overrides)

    with pytest.raises(McpConfigError) as caught:
        McpServerSpec(**fields)

    assert expected in str(caught.value)


def test_a_prefix_may_be_empty_but_not_a_typo() -> None:
    """空前缀是正常的 (不加前缀); 而带点 / 带空格的那种是写错了."""
    assert McpServerSpec(name="demo", command="python", tool_prefix="").prefix == ""
    prefixed = McpServerSpec(name="demo", command="python", tool_prefix="fs-")

    assert prefixed.prefix == "fs-"
