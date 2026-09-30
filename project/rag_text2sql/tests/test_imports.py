"""C01 回归: 从解释器 `Lib` 目录 / 第三方包借道导入标准库.

三处此前写的是:

- `app/core/log.py`: `from Lib import uuid` → `import uuid`
- `app/core/context.py`: `from sentry_sdk.utils import ContextVar` → 从 `contextvars` 取
- `app/services/meta.py`: `from Lib.pathlib import Path` → `from pathlib import Path`

IDE 自动导入把解释器的 `Lib` 目录当成了包. **在 Windows + 官方安装包的本机布局下
恰好能跑** (`C:\\Program Files\\Python\\...` 在 `sys.path` 里, `Lib` 被解析成
namespace package), 换 Linux / macOS / pyenv **立即 ImportError** —— 这类
「只在新机器上炸」的问题本地永远复现不了, 所以钉静态检查守它.

**判据为什么按 AST 而不是按运行时类型**: 最初写的是一条运行时断言
(`isinstance(request_id_ctx_var, contextvars.ContextVar)`), 实测它是**空的** ——
`sentry_sdk.utils.ContextVar is contextvars.ContextVar` 为 `True` (sentry 只是转出标准库
那一个), 于是误导入与正确写法**断言结果一样**. 按源码判导入才分得开.

**静态扫描之外还要真 import 一次**: 扫描只证明「源码里没有那种写法」, 不证明
「模块真能导入」—— 后者才是「克隆到干净机器能跑」那句话的内容. 第一版只做了扫描,
于是 `app.services.meta` 从头到尾没被执行过, 装配不完整的配置替身也没被发现.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = PROJECT_ROOT / "app"

# 真 import 一遍: 覆盖三处误导入所在模块 + 一条完整的装配链
# (`app.agent.graph` 会把 clients / repositories / 全部节点都拉起来,
#  配置替身少了 `DBConfig` 这类名字就会在这一步现形)
SMOKE_IMPORTS = (
    "app.core.context",
    "app.core.log",
    "app.services.meta",
    "app.clients.es",
    "app.clients.mysql",
    "app.agent.graph",
)


@pytest.mark.parametrize("module_name", SMOKE_IMPORTS)
def test_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


# 解释器的标准库目录被当成包导入 —— 任何形式都不允许
FORBIDDEN_MODULE_PREFIX = "Lib"

# 借道第三方包转出的标准库名字: (模块, 名字)
# 借道它会把绑定指到别人的实现上, 而本项目根本不需要 sentry
FORBIDDEN_IMPORTS: tuple[tuple[str, str], ...] = (("sentry_sdk.utils", "ContextVar"),)


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _offending_imports(tree: ast.AST) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name
                for alias in node.names
                if alias.name == FORBIDDEN_MODULE_PREFIX
                or alias.name.startswith(f"{FORBIDDEN_MODULE_PREFIX}.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == FORBIDDEN_MODULE_PREFIX or node.module.startswith(
                f"{FORBIDDEN_MODULE_PREFIX}."
            ):
                offenders.append(node.module)
            offenders.extend(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if (node.module, alias.name) in FORBIDDEN_IMPORTS
            )
    return offenders


@pytest.mark.parametrize(
    "path", _python_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT))
)
def test_no_borrowed_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = _offending_imports(tree)
    assert not offenders, f"{path} 有借道导入: {offenders}"
