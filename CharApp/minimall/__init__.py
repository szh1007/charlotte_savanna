"""minimall 包: 电商客服业务上下文 (CharApp 的第一个业务).

一句话理解: 用 `CharAgent` 框架给 `app/minimall` 商城装配一个能说人话的客服
助手 —— 框架不知道「商城」是什么, 业务全在这个包里。

装配线 (五步, 每一步归谁一眼看清)::

    买家身份 ──build_context──> RunContext(thread_id, tenant_id, user_id,
                                            payload={user_id, [一次性凭据]})
                                     │  await provider.provide(context)
                                     ▼
                                tuple[Tool, ...]  (18 个工具, 身份与凭据在闭包里)
                                     │  ChatSession(..., tools=..., hooks=护栏, ...)
                                     ▼
                                AgentLoop          (框架只认「一串工具」)

买家身份从哪来由**入口**决定: 命令行解析 argv (`cli.py`), 服务进程读 Django 转发的
请求头 (`server.py`); 两者都把它交给 `service.build_context` (唯一一处).

结构总览:

| 文件 | 管什么 |
|------|--------|
| `client.py`    | 商城内部端点的异步客户端 (18 个方法 = 18 个端点) |
| `config.py`    | 环境变量 → 客户端 / 服务进程配置 (地址 / 令牌 / 监听) 与压缩的旋钮 |
| `tools.py`     | 18 个工具 (9 只读 + 8 写 + 代付) + `build_tools` (身份与凭据进闭包) |
| `provider.py`  | `MinimallToolProvider`: 上下文 → 这次运行的工具集 |
| `guardrail.py` | 护栏 (写预算 8 次 + 单笔 5000 上限 + 代付挂起), 挂在框架的拦截点上 |
| `redaction.py` | 展示层脱敏: 工具事件换成中文短语 (敏感数据不出本进程, ADR-0003) |
| `service.py`   | 两入口共用的装配: 身份 → 上下文 → 会话 (L2.5 起连记录员与压缩器) |
| `cli.py`       | 命令行入口 (薄: 解析参数 / 读输入 / 打结果) |
| `server.py`    | 服务进程入口 (薄: 认证解析 / 转交装配 / 进程生命周期) |
| `prompt/`      | 客服系统提示词 + 版本清单 (业务提示词放业务目录, 框架目录里不留) |

边界 (PRD §4.2 / §4.3 定的两条, 加上 L2.5 划的三条):

- **能改数据, 但有闸**: L2 加了 8 个工具 (7 个会改数据: 加购 / 改量 / 移除 /
  清空 / 下单 / 取消 / 申请退款, 外加 1 个读退款的); 拦它们的 `guardrail.py`
  挂在框架的「工具执行前」这一个点上, 而不是在 7 个工具里各写一遍.
  **付款到 L3 才做** (issue 35): `pay_my_order` 是第 9 个写工具, 它**不停在护栏那
  一句「不行」上, 而是停在挂起上** —— 钱只有买家本人点头才动, 密码他本人在页面上
  输, 助手从头到尾看不见.
- **身份与一次性凭据都不进参数表**: 18 个工具的 schema 里搜不到买家 ID, 也搜不到
  `payment_password` —— 两者都由装配代码放进 `RunContext.payload`, 在 `build_tools`
  里进闭包 (PRD §4.2 / ADR-0015). 有一条测试按同一个判据守着这两件事.
- **会话的两种表示都归框架, 业务一行 SQL 都不写** (L2.5, ADR-0008 / 0009):
  账本由 `CharAgent/checkpoint/` 存, 给人看的记录由 `CharAgent/db/` 存, 会话列表
  与改名 / 置顶 / 删除三条路由也由框架提供. 业务这边只交出三样: 一个
  `PgDatabase()`, 两个身份字符串 (`tenant_id` / `user_id`), 以及压缩的旋钮.
- **Postgres 从此是硬依赖**: 商城的数据在 MySQL, 对话记录与快照在 Postgres ——
  两个库分别装着两件不同的事. 库不在线时进程照样起得来 (引擎是懒建的), 报错
  落在第一个买家的第一句问话上; **换一台机器 / 换一个库先跑
  `cd CharAgent && alembic upgrade head`**.
- **压缩的能力在框架, 旋钮在业务**: 阈值 / 保留轮数 / 工具结果截断 / 摘要开关 /
  水位线五项全在 `config.py`, 不填走默认. 框架默认**不压缩** (`compactor=None`
  与从前逐字一样) —— 业务这一侧是**显式**配上它的.

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
