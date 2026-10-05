"""两侧接上: 框架的消费侧 → 本业务的暴露侧 → (真 HTTP 的) 假商城.

这是 C14「两侧都做」那句话的**可复跑证据**. 前半段 (`McpToolProvider`, 属 CharAgent)
与后半段 (`python -m CharApp.minimall.mcp_server`, 属 CharApp) 各自已有自动用例,
但「它们真的接得上」此前只有一次手动记录 —— 那次要真 Django + 真数据 (真机验收的
范畴). 本文件把商城换成**同一个进程里的真 HTTP 服务**, 于是环上除了商城全是真的:
真子进程、真 stdio 传输、真 JSON-RPC 往返、真 HTTP 请求.

为什么这条用例在 **CharApp 侧**而不是 CharAgent 侧:

- 依赖方向是单向的 (`CharApp → CharAgent`, 见 `CharApp/docs/PLAN.md` §2): 业务可以
  用框架, 框架不认识业务 —— 而这里要起的正是业务那台 server, 写进框架的测试里就
  把那条线破了.
- 它要的那台 server 是**真的 `python -m` 子进程** (不是 in-memory 流): 跨进程之后
  respx 拦不住 httpx (那是本进程内的补丁), 所以商城必须是一个**真的** HTTP 服务
  —— 这正是本文件与 `test_mcp_server.py` 的分工 (那个用 in-memory 流 + respx,
  验协议与契约; 这个用真子进程 + 真 HTTP, 验「接得上」).

它证明的三件事 (缺一条都不叫接上):

1. 框架的 `McpToolProvider` 能把**本业务那台** server 的工具列出来 (11 个只读,
   写的一个不在);
2. 调一次真打到商城 (经两个进程边界 + 一次真 HTTP), 拿回的是商城的真字段;
3. **身份来自配置里那一个账户** —— 商城看到的 `X-User-Id` 就是 `CHARAPP_MCP_USER_ID`
   那个值, 客户端这一侧没有任何地方能改它.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from conftest import BUYER_ID, ORDER_LIST, PROFILE, TOKEN

from CharAgent.agent import RunContext
from CharAgent.mcp_client import McpServerSpec, McpToolProvider
from CharAgent.tool import execute_tool

# 仓库根 (子进程的 cwd: `python -m CharApp...` 靠它才 import 得到那个包)
REPO_ROOT = Path(__file__).resolve().parents[2]

# 暴露侧那台 server 上线的那 11 个 (写工具一个都不该出现)
EXPECTED_TOOLS = frozenset(
    {
        "search_products",
        "get_product_detail",
        "list_categories",
        "list_featured_products",
        "get_my_cart",
        "list_my_orders",
        "get_my_order",
        "get_my_profile",
        "list_my_addresses",
        "list_my_refunds",
        "search_knowledge",
    }
)


class _MallHandler(BaseHTTPRequestHandler):
    """假商城的一个请求: 记下它, 按路径回样本里的 JSON (不认识的路径回 404)."""

    def do_GET(self) -> None:
        mall = cast(_FakeMall, self.server)
        mall.requests.append((self.path, dict(self.headers)))
        # 路由只看**路径**: 查询串是参数, 不是另一个端点 (`self.path` 里带着它,
        # 直接拿去找路由会把一次带参数的正常调用变成 404)
        body = mall.routes.get(urlparse(self.path).path)
        payload = json.dumps(
            body if body is not None else {"detail": "假商城没有这个端点"},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        """关掉访问日志 —— 它默认往 stderr 写, 会把 pytest 的输出弄脏."""


class _FakeMall(ThreadingHTTPServer):
    """一个**真 HTTP** 的假商城 (样本照 `eval/fixtures.py` 抄, 与别的用例同源).

    attributes:
        routes: 路径 → 响应体 (`{"profile/": {...}}` 这种).
        requests: 收到的请求 (路径, 请求头), 按到达顺序 —— 用例据此断「谁被打到了、
            带着什么身份」.

    Note:
        端口是 0 (由系统分配), 于是并行的用例不会撞端口; 地址从 `base_url` 取.
    """

    daemon_threads = True

    def __init__(self, routes: dict[str, Any]) -> None:
        super().__init__(("127.0.0.1", 0), _MallHandler)
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []

    @property
    def base_url(self) -> str:
        """内部端点的根地址 (与 `client.DEFAULT_BASE_URL` 同一个形状)."""
        return f"http://127.0.0.1:{self.server_address[1]}/api/minimall/agent/"


@contextmanager
def fake_mall() -> Iterator[_FakeMall]:
    """起一台假商城, 用完收掉 (线程 + 套接字都还回去)."""
    mall = _FakeMall(
        {
            "/api/minimall/agent/profile/": PROFILE,
            "/api/minimall/agent/orders/": ORDER_LIST,
        }
    )
    thread = threading.Thread(target=mall.serve_forever, daemon=True)
    thread.start()
    try:
        yield mall
    finally:
        mall.shutdown()
        mall.server_close()
        thread.join(timeout=5)


def server_spec(mall: _FakeMall, tmp_path: Path) -> McpServerSpec:
    """暴露侧那台 server 的启动规格 (与 `.mcp.json` 那份同形).

    三处 env 都是刻意的:

    - `CHARAPP_BASE_URL` 指到假商城 (真的那台是 Django, 测试里不起);
    - `CHARAPP_MCP_USER_ID` 是**被代表的那个账户** —— 它与请求头的关系正是本文件
      第 3 条要断的事;
    - `CHARAPP_MODELSCOPE_ROOT` 指到一个**空目录**: 那台 server 起来之后会在后台
      预热两个本地模型 (7~8 秒), 而跨侧这条链要验的是「接得上」, 不是「模型加载
      多久」—— 模型缺失是设计内的降级路径 (记一条日志, 检索那条路不可用而已),
      别的工具一点都不受影响.
    """
    return McpServerSpec(
        name="minimall",
        command=sys.executable,
        args=("-m", "CharApp.minimall.mcp_server"),
        # cwd 与 PYTHONPATH 都给: 子进程靠前者 import 得到 CharApp, 后者是同一件事的
        # 另一种保险 (客户端从哪个目录起这个进程不由我们决定, 见 ADR 里那条实测)
        cwd=str(REPO_ROOT),
        env={
            "CHARAPP_BASE_URL": mall.base_url,
            "CHARAPP_MCP_USER_ID": str(BUYER_ID),
            "CHARAPP_INTERNAL_TOKEN": TOKEN,
            "CHARAPP_MODELSCOPE_ROOT": str(tmp_path / "no-models-here"),
        },
        call_timeout=30.0,
    )


def context() -> RunContext:
    """一个运行上下文 (MCP 工具不读它, 但 `provide` 的签名收着)."""
    return RunContext(thread_id="minimall:mcp:1", tenant_id="minimall", user_id="1")


async def test_the_framework_provider_swallows_our_own_mcp_server(
    tmp_path: Path,
) -> None:
    """框架的 `McpToolProvider` 接上本业务的 server, 调一次, 真数据回来.

    「接上」的定义就是这条用例的三段断言: 工具列得出来、调得通、身份是配置里那一个.
    """
    with fake_mall() as mall:
        provider = McpToolProvider([server_spec(mall, tmp_path)])
        await provider.start()
        try:
            tools = {tool.name: tool for tool in await provider.provide(context())}

            # 1. 列得出来: 就是暴露侧那 11 个只读工具 (写工具一个都不在)
            assert set(tools) == EXPECTED_TOOLS
            assert "place_order" not in tools

            # 2. 调得通: 经子进程边界 + stdio 往返 + 一次真 HTTP, 拿回商城的字段
            execution = await execute_tool(tools["get_my_profile"], arguments="{}")
            assert execution.ok, execution.error
            assert json.loads(execution.content) == PROFILE

            # 3. 身份是配置里那一个: 商城看到的就是 CHARAPP_MCP_USER_ID 那个值,
            #    而客户端这一侧 (模型的参数表 / 这次调用) 从头到尾没有出现过它
            path, headers = mall.requests[-1]
            assert path == "/api/minimall/agent/profile/"
            assert headers["X-User-Id"] == str(BUYER_ID)
            assert headers["X-Internal-Token"] == TOKEN
        finally:
            await provider.aclose()


async def test_a_collection_tool_also_reaches_the_mall(tmp_path: Path) -> None:
    """再看一条**带参数**的集合类工具 (订单列表): 参数真的走到了商城.

    与上一条只取单例不同, 这条带 `page_size` —— 于是「框架那道参数校验放行之后,
    参数真的被翻译成查询串发出去」也被钉住 (中间隔着四个进程内组件: 工具的合成
    签名 → 闭包 → 客户端 → HTTP).
    """
    with fake_mall() as mall:
        provider = McpToolProvider([server_spec(mall, tmp_path)])
        await provider.start()
        try:
            tools = {tool.name: tool for tool in await provider.provide(context())}

            execution = await execute_tool(
                tools["list_my_orders"], arguments='{"page_size": 5}'
            )

            assert execution.ok, execution.error
            assert json.loads(execution.content)["count"] == ORDER_LIST["count"]
            path, _ = mall.requests[-1]
            assert path == "/api/minimall/agent/orders/?page_size=5"
        finally:
            await provider.aclose()
