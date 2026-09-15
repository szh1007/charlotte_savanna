"""根门面导出测试 (issue 10): `CharAgent/__init__.py` 与各子包 __all__ 不许漂移.

为什么值得一条防漂移用例: 根门面从 P0-1 起只导出了 model + tool, 而
issue 05 / 06 / 07 / 08 **四处**都写着「agent / stream / hooks / retry /
checkpoint / db 尚未顶层导出, 同批处理」—— 四轮都往后推, 一直没做 (issue 10
一次补齐). 一条用例比四句待办更管用: 以后往子包 __all__ 里加了名字却忘了在根
门面补上, 这里立刻红.

两条刻意排除 (与根门面 docstring 写的是同两条):
- `tool` (小写, @tool 装饰器) —— 与子包 `CharAgent.tool` 同名, 导出会遮蔽包属性
- `client` 整个包 —— 它是应用入口 (`python -m CharAgent.client`), 不是库 API

被测对象是「两份 __all__ 的对应关系」, 不涉及运行时行为.
"""

from __future__ import annotations

import importlib

import pytest

import CharAgent

# 汇聚进根门面的框架包 (八层; client 刻意不在内, 见根门面 docstring)
FRAMEWORK_PACKAGES = (
    "model",
    "tool",
    "agent",
    "stream",
    "hooks",
    "retry",
    "checkpoint",
    "db",
)

# 同名遮蔽: 装饰器 `tool` 只能在 CharAgent.tool 里取 (见模块 docstring)
SHADOWED = frozenset({"tool"})


def _package_all(name: str) -> list[str]:
    """读一个子包门面声明的公共 API 清单."""
    module = importlib.import_module(f"CharAgent.{name}")
    return list(module.__all__)


@pytest.mark.parametrize("package", FRAMEWORK_PACKAGES)
def test_every_subpackage_export_exists_at_the_root(package: str) -> None:
    """子包 __all__ 里的每个名字, 根门面都必须导出 (且是同一个对象)."""
    missing = []
    for name in _package_all(package):
        if name in SHADOWED:
            continue
        if not hasattr(CharAgent, name):
            missing.append(name)
        else:
            submodule = importlib.import_module(f"CharAgent.{package}")
            assert getattr(CharAgent, name) is getattr(submodule, name), (
                f"{package}.{name} 与根门面的同名对象不是同一个 (重名遮蔽或导错了来源)"
            )
    assert missing == [], f"根门面缺少 {package} 的公共 API: {missing}"


def test_root_all_has_no_duplicates_and_no_ghosts() -> None:
    """根 __all__ 自身要干净: 不重复, 且每一条都真的存在."""
    names = list(CharAgent.__all__)

    assert len(names) == len(set(names)), "根 __all__ 有重复项"
    ghosts = [name for name in names if not hasattr(CharAgent, name)]
    assert ghosts == [], f"根 __all__ 声明了不存在的东西: {ghosts}"


def test_package_facades_do_not_collide_with_each_other() -> None:
    """八层之间不许有同名 API —— 根门面是它们的合集, 重名会让先导入的胜出."""
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for package in FRAMEWORK_PACKAGES:
        for name in _package_all(package):
            if name in SHADOWED:
                continue
            if name in seen:
                collisions.append(f"{name}: {seen[name]} 与 {package}")
            seen[name] = package
    assert collisions == [], f"子包之间存在同名公共 API: {collisions}"


def test_client_is_not_exported_at_the_root() -> None:
    """client 是应用入口不是库 API: 根门面不导出它的任何名字."""
    for name in _package_all("client"):
        assert name not in CharAgent.__all__, f"{name} 不该出现在根门面"
