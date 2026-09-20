"""minimall 包: 电商客服业务上下文 (CharApp 的第一个业务, L1a 只读).

一句话理解: 用 `CharAgent` 框架给 `app/minimall` 商城装配一个能说人话的客服
助手 —— 框架不知道「商城」是什么, 业务全在这个包里。

装配线 (四步, 每一步归谁一眼看清)::

    买家身份 ──build_context──> RunContext(thread_id, payload={user_id})
                                     │  await provider.provide(context)
                                     ▼
                                tuple[Tool, ...]  (9 个只读工具, 身份已在闭包里)
                                     │  ChatSession(..., tools=..., prompt_dir=业务目录)
                                     ▼
                                AgentLoop          (框架只认「一串工具」)

买家身份从哪来由**入口**决定: 命令行解析 argv (`cli.py`), 服务进程读 Django 转发的
请求头 (`server.py`); 两者都把它交给 `service.build_context` (唯一一处).

结构总览:

| 文件 | 管什么 |
|------|--------|
| `client.py`   | 商城内部端点的异步客户端 (9 个方法 = 9 个只读端点) |
| `config.py`   | 环境变量 → 客户端 / 服务进程配置 (地址 / 令牌 / 监听) |
| `tools.py`    | 9 个只读工具 + `build_tools` (身份在这里进闭包) |
| `provider.py` | `MinimallToolProvider`: 上下文 → 这次运行的工具集 |
| `service.py`  | 两入口共用的装配: 身份 → 上下文 → 会话 (CLI 与 server 同一份) |
| `cli.py`      | 命令行入口 (薄: 解析参数 / 读输入 / 打结果) |
| `server.py`   | 服务进程入口 (薄: 认证解析 / 转交装配 / 进程生命周期) |
| `prompt/`     | 客服系统提示词 (业务提示词放业务目录, 框架目录里不留) |

边界 (PRD §4.2 / §4.3 定的两条):

- **只读**: 加购物车、下单、付款、取消、退款都是第二阶段的事, 这个包里没有
  任何写操作, 商城侧也没有对应的写接口.
- **身份不进参数表**: 9 个工具的 schema 里搜不到买家 ID —— 它由装配代码放进
  `RunContext.payload`, 在 `build_tools` 里进闭包. 有一条测试专门守这条.

用法::

    python -m CharApp.minimall.cli --user-id 3
"""

from __future__ import annotations

from CharApp.minimall.client import (
    DEFAULT_BASE_URL,
    MinimallClient,
    MinimallError,
    MinimallNotFoundError,
)
from CharApp.minimall.config import MinimallConfigError, client_from_env
from CharApp.minimall.provider import PAYLOAD_USER_ID, MinimallToolProvider, buyer_id
from CharApp.minimall.tools import build_tools

__all__ = [
    "DEFAULT_BASE_URL",
    "PAYLOAD_USER_ID",
    "MinimallClient",
    "MinimallConfigError",
    "MinimallError",
    "MinimallNotFoundError",
    "MinimallToolProvider",
    "build_tools",
    "buyer_id",
    "client_from_env",
]
