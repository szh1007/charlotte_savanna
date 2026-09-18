"""minimall 业务包的配置读取: 商城内部端点的地址与共享令牌从哪来.

一句话理解: 环境变量 → 客户端实例的翻译层, 与 `checkpoint/config.py`、
`db/config.py` 同一套做法 —— 变量名在这里声明成常量, 不在业务代码里散着搜
字符串 (审计「谁读了 env」时应当一眼看得见)。

两个变量的值都写在**仓库根** `.env` 里 (与 `CHARPLOT_*` 同一个文件, 不提交;
模板见 `.env.example`)。`CHARAPP_INTERNAL_TOKEN` 必须与商城侧同值 —— 不一样的话
商城一律拒绝 (那边 fail closed), 助手只会报「工具执行时发生内部错误」。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from CharApp.minimall.client import DEFAULT_BASE_URL, MinimallClient

# 内部端点前缀 (不给则用本机 Django 开发服务器, 见 DEFAULT_BASE_URL)
ENV_BASE_URL = "CHARAPP_MINIMALL_BASE_URL"

# 内部令牌 (与商城侧 CHARAPP_INTERNAL_TOKEN 同值; 两边读的是同一个名字, 但归属
# 不同 —— 商城那份在 settings/base.py, 这里这份在业务侧)
ENV_TOKEN = "CHARAPP_INTERNAL_TOKEN"


class MinimallConfigError(Exception):
    """业务侧配置缺失或非法.

    与框架的 `ModelConfigError` / `CheckpointConfigError` 同类: 是**启动期**错误
    (命令敲错了 / 环境没配好), 命令行入口把它翻成一行人话, 不打印 traceback。
    """


def client_from_env(env: Mapping[str, str] | None = None) -> MinimallClient:
    """按环境变量建一个商城客户端 (一个进程一个, 退出时 `aclose()`)。

    Args:
        env: 环境变量映射; None 表示读 `os.environ` (测试传一个字典来钉死配置)。

    Returns:
        MinimallClient: 已装好地址与令牌的客户端 (身份不在这里 —— 每个方法自己收)。

    Raises:
        MinimallConfigError: `CHARAPP_INTERNAL_TOKEN` 没配。这一条**不兜底**: 令牌空着
            时商城会拒掉每一个请求, 与其让使用者对着「内部错误」猜, 不如当场说清
            该去哪儿配。
    """
    values = os.environ if env is None else env
    token = (values.get(ENV_TOKEN) or "").strip()
    if not token:
        raise MinimallConfigError(
            f"{ENV_TOKEN} 未配置 —— 助手调不动商城的内部端点 (那边没有令牌就全拒)。"
            f"请在仓库根 .env 里填上与商城同值的令牌, 模板见 .env.example"
        )
    base_url = (values.get(ENV_BASE_URL) or "").strip() or DEFAULT_BASE_URL
    return MinimallClient(base_url=base_url, token=token)


__all__ = ["ENV_BASE_URL", "ENV_TOKEN", "MinimallConfigError", "client_from_env"]
