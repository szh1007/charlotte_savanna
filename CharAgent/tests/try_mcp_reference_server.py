"""手动验证: 拿**官方参考 server** 试一次消费侧接缝 (不进默认用例).

用法 (在仓库根跑, **用仓库 `.venv` 的 python**; 需要 node / npx 与网络, 首次会
下载那个包)::

    .venv/Scripts/python.exe CharAgent/tests/try_mcp_reference_server.py [目录]

为什么单独一个脚本而不是一条用例: 默认用例**不触网**是这套测试的纪律 (见
conftest 开头), 而官方那几个参考实现要么走 npx (要 node + 首次下载)、要么要真实
的文件系统布局. 契约用例的对端因此是本仓自己的 fixture server
(`tests/fixtures/mcp/demo_server.py`, 真协议真子进程零依赖); 而「我们的抽象能原样
装下**别人家**写的 server」这句话, 由这个脚本当场跑给人看 —— 它做的事与用例逐字
一样 (列工具 → 转 `Tool` → 调一次), 只是对端换成了 npm 上的那个.

它同时是一份可读的接线样例: 装配一段就是这么长.

**默认直连 npx 缓存里的 node 入口, 而不是 `npx -y <包>`** (2026-10-05 的对照实验):
同一条链路、同一份实现, 只换启动方式 ——

| 启动方式 | 实测 |
|---|---|
| `npx -y <包> <目录>` | 3 次失败 (连接在 `tools/list` 或调用时断, 对端连横幅都没打) |
| `node <缓存>/dist/index.js <目录>` | **5/5 全过** (横幅、14 个工具、调用全成) |

机制没钉死 (「无横幅」也可能是 npm 把子进程的 stderr 吞了, 不是 server 没起), 但
结论可操作: **绕开 npx 那一层更稳**. 缓存为空时回退到 npx —— 那一趟的作用就是把包
下下来 (首次要网络), 之后再跑就走直连那条.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

# 直接把**脚本的路径**敲给 python 时, `sys.path[0]` 是脚本所在目录
# (`CharAgent/tests/`) 而不是当前目录 —— 于是从仓库根跑也 import 不到
# `CharAgent`. 这里显式补上仓库根 (与 `tests/record_llm_samples.py` 同一手).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from CharAgent.mcp_client import McpServerSpec, McpToolProvider
    from CharAgent.tool import Tool, execute_tool
except ModuleNotFoundError as _exc:  # pragma: no cover - 手工脚本的友好提示
    raise SystemExit(
        f"import 失败 ({_exc.name}) —— 请用**仓库 .venv 的 python** 跑这个脚本"
        f" (系统 python 里没有 mcp SDK 与项目依赖), 例如:\n"
        f"    .venv/Scripts/python.exe "
        f"CharAgent/tests/try_mcp_reference_server.py <目录>"
    ) from _exc

# 官方参考 server (npx 包名); 起法就是它们文档里那一行
REFERENCE_SERVER = "@modelcontextprotocol/server-filesystem"

# 要列的第一个工具 (filesystem server 提供的只读工具之一)
DEMO_TOOL = "list_directory"


def cached_entry() -> Path | None:
    """在 npx 缓存里找那份实现 (`.../_npx/<hash>/node_modules/<包>/dist/index.js`).

    Returns:
        Path | None: 找到就是那个入口文件; 缓存为空时 None (调用方回退到 npx,
        那一趟的作用就是把包下下来).
    """
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_npx"
    matches = sorted(root.glob(f"*/node_modules/{REFERENCE_SERVER}/dist/index.js"))
    return matches[0] if matches else None


def spec_for(directory: Path) -> McpServerSpec:
    """那台 server 的启动规格: 优先直连缓存里的 node 入口, 缓存空才回退 npx.

    两条路的实测差别见模块 docstring 那张表 (直连 5/5, 经 npx 3 次失败).

    **目录一律解析成绝对路径再传**: 对端拿到相对路径时按**它自己的 cwd** 解析, 而
    那个 cwd 不由我们决定 (中间隔着 npx 与它拉起的 node) —— 一行 `resolve()` 把这个
    变量从等式里拿掉, 代价为零.
    """
    entry = cached_entry()
    if entry is not None:
        return McpServerSpec(
            name="filesystem",
            command=shutil.which("node") or "node",
            args=(str(entry), str(directory.resolve())),
            call_timeout=60.0,
        )
    return McpServerSpec(
        name="filesystem",
        command="npx",
        args=("-y", REFERENCE_SERVER, str(directory.resolve())),
        call_timeout=60.0,  # 首次 npx 要下载, 给宽一点
    )


def describe(tool: Tool) -> str:
    """一个工具的一行摘要 (名字 + 参数名 + 说明首句)."""
    properties = tool.parameters.get("properties") or {}
    params = ", ".join(properties) or "(无参数)"
    first_line = tool.description.strip().splitlines()[0]
    return f"  {tool.name}({params}) —— {first_line}"


async def main(directory: Path) -> int:
    """列工具 → 调一次 → 打印结果; 返回退出码."""
    provider = McpToolProvider([spec_for(directory)])
    entry = cached_entry()
    fallback = f"npx -y {REFERENCE_SERVER} (缓存为空)"
    route = f"node {entry}" if entry is not None else fallback
    print(f"起对端: {route} {directory.resolve()}", flush=True)
    print("(它的 stderr 会混在下面 —— 参考实现往那儿打横幅与警告)", flush=True)
    await provider.start()
    try:
        tools = await provider.provide(None)  # 上下文对 MCP 工具没有用, 见 provider
        print(f"从 {REFERENCE_SERVER} 发现 {len(tools)} 个工具:", flush=True)
        for tool in tools:
            print(describe(tool), flush=True)

        target = next((tool for tool in tools if tool.name == DEMO_TOOL), None)
        if target is None:
            print(f"(这台 server 没有 {DEMO_TOOL}, 跳过调用那一步)", flush=True)
            return 0
        execution = await execute_tool(
            target, arguments=json.dumps({"path": str(directory.resolve())})
        )
        print(
            f"\n调一次 {DEMO_TOOL}(path={directory.resolve()}): ok={execution.ok}",
            flush=True,
        )
        print((execution.content or execution.error or "")[:600], flush=True)
        if not execution.ok:
            # 失败时把**底层原因**也打出来: 回填给模型的那句是统一的内部错误文案
            # (框架的规矩), 而手工排障要看的恰恰是它盖住的那个异常
            print(f"底层原因: {execution.exception!r}", flush=True)
        return 0 if execution.ok else 1
    finally:
        await provider.aclose()


if __name__ == "__main__":
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    raise SystemExit(asyncio.run(main(target_dir)))
