"""minimall 业务包的配置读取: 商城内部端点的地址与共享令牌从哪来.

一句话理解: 环境变量 → 客户端实例的翻译层, 与 `checkpoint/config.py`、
`db/config.py` 同一套做法 —— 变量名在这里声明成常量, 不在业务代码里散着搜
字符串 (审计「谁读了 env」时应当一眼看得见)。

两个变量的值都写在**仓库根** `.env` 里 (与 `CHARPLOT_*` 同一个文件, 不提交;
模板见 `.env.example`)。`CHARAPP_INTERNAL_TOKEN` 必须与商城侧同值 —— 不一样的话
商城一律拒绝 (那边 fail closed), 助手只会报「工具执行时发生内部错误」。

服务进程 (server.py) 的两个变量 (`CHARAPP_SERVER_*`) 也在这里读: 监听地址与
端口是**部署期**配置, 与「助手怎么跟商城说话」是两件事, 但同属「业务读 env」
这一个翻译层, 放一起才看得全。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from CharApp.minimall.client import DEFAULT_BASE_URL, MinimallClient

# 内部端点前缀 (不给则用本机 Django 开发服务器, 见 DEFAULT_BASE_URL)
ENV_BASE_URL = "CHARAPP_BASE_URL"

# 内部令牌 (与商城侧 CHARAPP_INTERNAL_TOKEN 同值; 两边读的是同一个名字, 但归属
# 不同 —— 商城那份在 settings/base.py, 这里这份在业务侧)
ENV_TOKEN = "CHARAPP_INTERNAL_TOKEN"

# 服务进程的监听地址与端口 (与既有 CHARAPP_* 同族)
ENV_SERVER_HOST = "CHARAPP_SERVER_HOST"
ENV_SERVER_PORT = "CHARAPP_SERVER_PORT"

# 思考模式开关 (**服务端专用**: 命令行入口用 `--no-thinking`, 不看这个变量)
ENV_THINKING = "CHARAPP_THINKING"

# 布尔配置认得的写法 (跟着 .env 的习惯走: 1/true/yes/on —— 与 db/config.py 同一套)
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

# 默认只监听本机: 这个服务面向同一台机器上的 Django 转发层, 不直接对外
# (浏览器 ↔ Django 才是唯一验证身份的边界, 见 PRD §4.10)。端口取 1007, 与
# PLAN §4 架构图里那个数无关 —— 它是本机默认值, 换环境改 CHARAPP_SERVER_PORT 即可。
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 1007


class MinimallConfigError(Exception):
    """业务侧配置缺失或非法.

    与框架的 `ModelConfigError` / `CheckpointConfigError` 同类: 是**启动期**错误
    (命令敲错了 / 环境没配好), 两个入口都把它翻成一行人话, 不打印 traceback。
    """


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """客服服务进程怎么跑 (监听地址 / 端口 / 校验令牌).

    attributes:
        host: 监听地址。
        port: 监听端口。
        token: 校验 Django 转发件用的内部令牌 (与商城侧同值)。**一个进程一个**:
            同一份值既用来认出「转发方是自己人」, 也被客户端拿去打商城 —— 同一
            信任域里的同一个口令 (PRD §4.10)。`repr=False`: 共享秘密不该跟着
            对象被打印进 traceback 或日志。
    """

    host: str
    port: int
    token: str = field(repr=False)


def token_from_env(env: Mapping[str, str] | None = None) -> str:
    """读内部令牌 (**唯一一处**); 空着就抛。

    两个方向读的是同一个值: 客户端拿它打商城, 服务端拿它认转发件。

    Raises:
        MinimallConfigError: `CHARAPP_INTERNAL_TOKEN` 没配。这一条**不兜底**:
            令牌空着时商城会拒掉每一个请求 (服务端也该一律拒绝), 与其让使用者
            对着「内部错误」猜, 不如当场说清该去哪儿配。
    """
    values = os.environ if env is None else env
    token = (values.get(ENV_TOKEN) or "").strip()
    if not token:
        raise MinimallConfigError(
            f"{ENV_TOKEN} 未配置 —— 助手调不动商城的内部端点 (那边没有令牌就全拒)。"
            f"请在仓库根 .env 里填上与商城同值的令牌, 模板见 .env.example"
        )
    return token


def client_from_env(env: Mapping[str, str] | None = None) -> MinimallClient:
    """按环境变量建一个商城客户端 (一个进程一个, 退出时 `aclose()`)。

    Args:
        env: 环境变量映射; None 表示读 `os.environ` (测试传一个字典来钉死配置)。

    Returns:
        MinimallClient: 已装好地址与令牌的客户端 (身份不在这里 —— 每个方法自己收)。

    Raises:
        MinimallConfigError: `CHARAPP_INTERNAL_TOKEN` 没配 (见 `token_from_env`)。
    """
    values = os.environ if env is None else env
    base_url = (values.get(ENV_BASE_URL) or "").strip() or DEFAULT_BASE_URL
    return MinimallClient(base_url=base_url, token=token_from_env(values))


def thinking_from_env(env: Mapping[str, str] | None = None) -> bool | None:
    """读思考模式开关 (**服务端专用**); 不填 = 不传该参数 (走上游默认: 开启).

    三态而不是两态, 与框架那条契约对齐 (见 `model/protocol.py` 的 `thinking`):
    None = 不传 (上游默认开启) / True = 显式开 / False = 关. 空值因此**不等于**
    False —— 这也是「没配就与从前逐字一样」的原因.

    Args:
        env: 环境变量映射; None 表示读 `os.environ`。

    Returns:
        bool | None: 关 / 开 / 不传 (三态)。

    Raises:
        MinimallConfigError: 值不认识。这个开关直接决定每次请求的 token 与延迟,
            写错了该当场说清, 而不是猜一个方向.
    """
    values = os.environ if env is None else env
    raw = (values.get(ENV_THINKING) or "").strip().lower()
    if not raw:
        return None
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise MinimallConfigError(
        f"{ENV_THINKING} 只认 1/true/yes/on 或 0/false/no/off "
        f"(不填 = 上游默认开启), 实际: {raw!r}"
    )


def server_config_from_env(env: Mapping[str, str] | None = None) -> ServerConfig:
    """按环境变量读服务进程的监听地址与端口; 没配就用默认值 (本机 1007)。

    Args:
        env: 环境变量映射; None 表示读 `os.environ`。

    Returns:
        ServerConfig: 监听地址 + 端口 + 校验令牌。

    Raises:
        MinimallConfigError: 令牌没配, 或端口不是 1~65535 的整数 (当场说清,
            而不是让 uvicorn 抛一句听不懂的)。
    """
    values = os.environ if env is None else env
    raw_port = (values.get(ENV_SERVER_PORT) or "").strip()
    port = DEFAULT_SERVER_PORT
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise MinimallConfigError(
                f"{ENV_SERVER_PORT} 不是整数: {raw_port!r}"
            ) from exc
        if not 1 <= port <= 65535:
            raise MinimallConfigError(f"{ENV_SERVER_PORT} 超出 1~65535: {port}")
    return ServerConfig(
        host=(values.get(ENV_SERVER_HOST) or "").strip() or DEFAULT_SERVER_HOST,
        port=port,
        token=token_from_env(values),
    )


__all__ = [
    "DEFAULT_SERVER_HOST",
    "DEFAULT_SERVER_PORT",
    "ENV_BASE_URL",
    "ENV_SERVER_HOST",
    "ENV_SERVER_PORT",
    "ENV_THINKING",
    "ENV_TOKEN",
    "MinimallConfigError",
    "ServerConfig",
    "client_from_env",
    "server_config_from_env",
    "thinking_from_env",
    "token_from_env",
]
