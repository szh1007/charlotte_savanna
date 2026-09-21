"""minimall 包: 电商客服业务上下文 (CharApp 的第一个业务).

一句话理解: 用 `CharAgent` 框架给 `app/minimall` 商城装配一个能说人话的客服
助手 —— 框架不知道「商城」是什么, 业务全在这个包里。

装配线 (五步, 每一步归谁一眼看清)::

    买家身份 ──build_context──> RunContext(thread_id, payload={user_id})
                                     │  await provider.provide(context)
                                     ▼
                                tuple[Tool, ...]  (17 个工具, 身份已在闭包里)
                                     │  ChatSession(..., tools=..., hooks=护栏, ...)
                                     ▼
                                AgentLoop          (框架只认「一串工具」)

买家身份从哪来由**入口**决定: 命令行解析 argv (`cli.py`), 服务进程读 Django 转发的
请求头 (`server.py`); 两者都把它交给 `service.build_context` (唯一一处).

结构总览:

| 文件 | 管什么 |
|------|--------|
| `client.py`    | 商城内部端点的异步客户端 (17 个方法 = 17 个端点) |
| `config.py`    | 环境变量 → 客户端 / 服务进程配置 (地址 / 令牌 / 监听) |
| `tools.py`     | 17 个工具 (9 只读 + 8 写) + `build_tools` (身份在这里进闭包) |
| `provider.py`  | `MinimallToolProvider`: 上下文 → 这次运行的工具集 |
| `guardrail.py` | 写操作护栏 (预算 8 次 + 单笔 5000 上限), 挂在框架的拦截点上 |
| `service.py`   | 两入口共用的装配: 身份 → 上下文 → 会话 (CLI 与 server 同一份) |
| `cli.py`       | 命令行入口 (薄: 解析参数 / 读输入 / 打结果) |
| `server.py`    | 服务进程入口 (薄: 认证解析 / 转交装配 / 进程生命周期) |
| `prompt/`      | 客服系统提示词 + 版本清单 (业务提示词放业务目录, 框架目录里不留) |

边界 (PRD §4.2 / §4.3 定的两条):

- **能改数据, 但有闸**: L2 加了 8 个工具 (7 个会改数据: 加购 / 改量 / 移除 /
  清空 / 下单 / 取消 / 申请退款, 外加 1 个读退款的); 拦它们的 `guardrail.py`
  挂在框架的「工具执行前」这一个点上, 而不是在 7 个工具里各写一遍.
  **付款仍然不做** —— 那是 L3 的挂起 (买家本人在页面上输支付密码).
- **身份不进参数表**: 17 个工具的 schema 里搜不到买家 ID —— 它由装配代码放进
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
    MinimallRefusalError,
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
    "MinimallRefusalError",
    "MinimallToolProvider",
    "build_tools",
    "buyer_id",
    "client_from_env",
]
