"""测试夹具: 配置替身.

一个绕不开的前置: `app/conf/app_config.py` 在 **import 期** 读 `conf/app_config.yaml`,
而那个文件是本地私有配置 (根 .gitignore 的 `*.yaml` 挡着, 不进仓库). 于是
「克隆下来就能跑测试」差的正是这一个文件.

处置: 文件在场就用真的; 不在场才装一份替身. **替身不是手抄的镜像** —— 它把真模块里
`config_file = ...` 之前的 dataclass 定义**原样 exec 出来**, 再按 dataclass 的字段
递归造一份占位实例. 这样真模块加字段时替身自动跟上, 不会漂.

为什么必须连 `DBConfig` / `ESConfig` 这些类一起给: `app/clients/*.py` 是
`from app.conf.app_config import DBConfig, app_config` —— 只给 `app_config` 的话,
干净机器上 import `app.clients.*` 与 `app.agent.graph` 当场 ImportError.

**生产入口 (`main.py`) 不走这条路** —— 那边仍然是「读不到就报错」, 不静默兜底.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
CONFIG_FILE = PROJECT_ROOT / "conf" / "app_config.yaml"
REAL_CONFIG_SOURCE = PROJECT_ROOT / "app" / "conf" / "app_config.py"

# 末尾「读 yaml + 合并」那几行正是干净机器上跑不起来的原因, 从它前面切开
_LOAD_CUT_MARKER = "config_file = "

# 占位值刻意取「空」而不是 127.0.0.1 那种像真的地址: 万一将来有 import 期就连接的
# 代码, 空值会当场失败, 而不是悄悄连到本机某个真服务上
_PLACEHOLDERS: dict[type, object] = {bool: False, int: 0, str: ""}


def _instantiate(config_class: type) -> object:
    """按 dataclass 的字段递归造一个占位实例."""
    field_values = {}
    for field in dataclasses.fields(config_class):
        field_type = field.type
        if isinstance(field_type, str):
            # 前向引用 —— 真模块现在不用它, 出了就当场炸, 别静默给个空值
            raise RuntimeError(
                f"{config_class.__name__}.{field.name} 的类型是字符串 "
                f"{field_type!r}, 替身解析不了; 这个替身要跟着改"
            )
        if dataclasses.is_dataclass(field_type):
            field_values[field.name] = _instantiate(field_type)
        else:
            field_values[field.name] = _PLACEHOLDERS.get(field_type, "")
    return config_class(**field_values)


def _install_config_stub() -> None:
    source = REAL_CONFIG_SOURCE.read_text(encoding="utf-8")
    if _LOAD_CUT_MARKER not in source:
        raise RuntimeError(
            f"{REAL_CONFIG_SOURCE} 的结构变了 "
            f"(找不到 {_LOAD_CUT_MARKER!r}), 替身要跟着改"
        )

    stub = types.ModuleType("app.conf.app_config")
    # 必须先注册再 exec: `@dataclass` 处理类时要按 `cls.__module__` 回查 `sys.modules`,
    # 注册晚一步就是 `'NoneType' object has no attribute '__dict__'`
    sys.modules["app.conf.app_config"] = stub

    head = source.split(_LOAD_CUT_MARKER, 1)[0]
    # `dont_inherit=True` 不是可选的: `compile()` **会继承调用方的 `__future__` 标志**,
    # 而本文件自己带 `from __future__ import annotations` —— 不关掉的话, exec 出来的
    # dataclass 注释全变成字符串 (`field.type == 'LoggingConfig'` 而不是那个类),
    # 下面按类型递归就废了. 实测过: 同一段源码在带/不带 future 的上下文里 exec,
    # `isinstance(field.type, str)` 分别是 True / False.
    code = compile(head, str(REAL_CONFIG_SOURCE), "exec", dont_inherit=True)
    exec(code, stub.__dict__)
    stub.app_config = _instantiate(stub.AppConfig)


if not CONFIG_FILE.exists():  # pragma: no cover - 本机有真配置时不走这条
    _install_config_stub()
