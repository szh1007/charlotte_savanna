"""根门面导出测试: `CharAgent/__init__.py` 与各子包 __all__ 不许漂移.

为什么值得一条防漂移用例: 根门面早期只导出了 model + tool, 而
「agent / stream / hooks / retry / checkpoint / db 尚未顶层导出, 同批处理」
这句待办**四处**都写着 —— 四轮都往后推, 一直没做 (最后一次性补齐). 一条用例
比四句待办更管用: 以后往子包 __all__ 里加了名字却忘了在根门面补上, 这里立刻红.

五条刻意排除 (与根门面 docstring 写的是同一批, 理由各不同):
- `tool` (小写, @tool 装饰器) —— 与子包 `CharAgent.tool` 同名, 导出会遮蔽包属性
- `client` 整个包 —— 它是应用入口 (`python -m CharAgent.client`), 不是库 API
- `server` 整个包 —— 它要 web 栈 (可选依赖组 `charagent[server]`): 根门面是
  「装了这个包就能用」的库 API, 不该把一个可选的 web 框架变成硬依赖
- `mcp_client` 整个包 —— 同属「要额外条件才能用」那一类 (可选依赖组
  `charagent[mcp]`): MCP SDK 自己会把 web 栈拖进来 (1.29 实测: `import mcp`
  之后 starlette 就在 `sys.modules` 里了), 于是它进根门面等于把上一条保证作废
- `eval` 整个包 —— 它要真 API key 才跑得动 (跑分打的是真模型), 同属「要额外条件
  才能用」那一类. 名字与 `client` / `server` / `mcp_client` 一起列在
  `EXCLUDED_PACKAGES` 里, 于是「它真的不在根门面」是被断言守着的, 而不是碰巧没写

被测对象是「两份 __all__ 的对应关系」, 不涉及运行时行为. 两条例外各自起一个子进程
(「import 根门面不拖 web 栈」与「不拖 MCP SDK」) —— 那两件事只有真 import 一次才
看得见, 在测试进程里看 `sys.modules` 等于测了个假的.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import CharAgent

# 汇聚进根门面的框架包 (十一层; client / server / mcp_client / eval 刻意不在内)
FRAMEWORK_PACKAGES = (
    "model",
    "tool",
    "agent",
    "stream",
    "hooks",
    "retry",
    "checkpoint",
    "db",
    "prompt",
    "redact",
    "structured_logging",
)

# 同一份名单的「出口目」: 它们也是框架的包, 但刻意不进门面 (见模块 docstring)
EXCLUDED_PACKAGES = ("client", "server", "mcp_client", "eval")

# 同名遮蔽: 装饰器 `tool` 只能在 CharAgent.tool 里取 (见模块 docstring)
SHADOWED = frozenset({"tool"})

# 仓库根 (跑子进程时当 cwd: `python -c "import CharAgent"` 靠它找到包)
REPO_ROOT = Path(__file__).resolve().parents[2]


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
    """十一层之间不许有同名 API —— 根门面是它们的合集, 重名会让先导入的胜出."""
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


@pytest.mark.parametrize("package", EXCLUDED_PACKAGES)
def test_the_excluded_packages_are_not_exported_at_the_root(package: str) -> None:
    """四个刻意排除的包, 一个名字都不许上根门面 (理由见模块 docstring)."""
    for name in _package_all(package):
        assert name not in CharAgent.__all__, f"{name} 不该出现在根门面"


def test_the_record_double_is_not_exported_at_the_root() -> None:
    """记录层的假库也不上门面 —— **包内的某个模块**不上, 与那四个包同一件事.

    `db/testing.py` 的处境与 `db/config.py` 的两个环境变量名常量一样: 是「要用库
    的东西」那一层, 不是「装了 charagent 就有」的 API (需要的人按完整路径取).

    为什么值得一条断言: 顺手在 `db/__init__.py` 里加一行 `from .testing import
    FakeRecordDatabase` 不会报任何错, 只会让「装了就有这些 API」这句话里混进一个
    **测试替身** —— 而那是别人写测试时才该出现的东西. 有一条用例在, 那一刻当场红.
    """
    assert "FakeRecordDatabase" not in _package_all("db")
    assert not hasattr(CharAgent, "FakeRecordDatabase")
    # `logging/testing.py` 那一对是同一类东西 (两个项目的用例共用的支撑, 不是库 API)
    assert "logging_to" not in _package_all("structured_logging")
    assert "restore_logging" not in _package_all("structured_logging")
    assert not hasattr(CharAgent, "logging_to")


def test_importing_the_root_does_not_pull_in_the_web_stack() -> None:
    """`import CharAgent` 不带 web 依赖 —— 「server 是可选层」这句话的证据.

    为什么要起子进程: 在测试进程里看 sys.modules 等于测了个假的 —— 别的用例早就
    把 fastapi 导进来了. 只有干净的进程里 import 一次, 才能验「装了这个包就能
    用」这句话 (不装 fastapi 也用得了 agent 框架).

    这条同时是根门面的守卫: 哪天有人顺手把 server 加进根门面, 它立刻红 ——
    server 的名字只能从 `CharAgent.server` 里取.
    """
    script = (
        "import sys, CharAgent; "
        "print(any(name in sys.modules for name in ('fastapi', 'starlette')))"
    )

    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        check=True,
    )

    assert proc.stdout.strip() == "False", (
        f"根门面把 web 栈拖进来了: {proc.stdout.strip()} (server 该留在可选层)"
    )


def test_importing_the_root_does_not_pull_in_the_mcp_sdk() -> None:
    """`import CharAgent` 也不带 MCP SDK —— 「mcp_client 是可选层」这句话的证据.

    与上一条其实是同一件事的两面 (MCP SDK 自己会把 starlette 拖进来), 分开写是
    因为**失败时指向的地方不同**: 那一条指向 `server`, 这一条指向「谁把
    `mcp_client` 导进了根门面」.
    """
    script = (
        "import sys, CharAgent; "
        "print(any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules))"
    )

    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        check=True,
    )

    assert proc.stdout.strip() == "False", (
        f"根门面把 MCP SDK 拖进来了: {proc.stdout.strip()} "
        f"(它该留在 charagent[mcp] 那一层)"
    )
