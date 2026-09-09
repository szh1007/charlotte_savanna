"""函数签名内省: 参数条目 / Annotated 拆解 / docstring 描述提取.

两引擎共用 (engine_pydantic / engine_manual): 从工具函数签名收集 schema
生成的输入 —— 具名参数 (含类型注解校验)、注解元数据 (Annotated)、
Google Style docstring 的参数描述与工具首段说明.
"""

from __future__ import annotations

import inspect
import re
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from CharAgent.tool.utils.errors import ToolConfigError


def param_entries(fn: Any, *, engine: str) -> list[inspect.Parameter]:
    """签名参数 (排除 *args/**kwargs), 缺类型注解时报配置错误.

    被 engine_pydantic / engine_manual 跨模块共享, 故公开命名
    (下划线前缀仅限模块内部私有, 见 split_annotated 同规则).
    """
    sig = inspect.signature(fn)
    hints = get_type_hints(fn, include_extras=True)
    entries: list[inspect.Parameter] = []
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ToolConfigError(
                f"{engine} 引擎不支持 *args/**kwargs (参数 {param.name}), "
                f"schema 需要具名参数"
            )
        annotation = hints.get(param.name, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise ToolConfigError(
                f"{engine} 引擎要求参数 {param.name} 有类型注解 "
                f"(JSON schema 由注解生成)"
            )
        entries.append(param)
    return entries


def split_annotated(ann: Any) -> tuple[Any, tuple[Any, ...]]:
    """Annotated 拆为 (基础类型, metadata); 非 Annotated 原样返回 (ann, ())."""
    if get_origin(ann) is Annotated:
        base, *meta = get_args(ann)
        return base, tuple(meta)
    return ann, ()


def parse_google_args_doc(fn: Any) -> dict[str, str]:
    """提取 docstring Google Style ``Args:`` 节的参数描述 (续行合并).

    兼容中文 docstring; 遇空行或 Returns/Yields/Raises 章节结束.
    """
    doc = inspect.getdoc(fn)
    if not doc:
        return {}
    out: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in doc.splitlines():
        stripped = line.strip()
        if not in_args:
            in_args = stripped == "Args:"
            continue
        if not stripped:
            in_args = False  # 空行结束 Args 节
            current = None
            continue
        match = re.match(r"^([a-z_]\w*):\s*(.*)$", stripped)
        if match:
            current = match.group(1)
            out[current] = match.group(2)
        elif re.match(r"^[A-Z][A-Za-z]*:", stripped):
            # Returns/Yields/Raises 等章节头 (无空行分隔时) 不并入参数描述
            current = None
        elif current is not None:
            # 续当前参数描述 (多行)
            out[current] = f"{out[current]} {stripped}".strip()
    return out


def first_paragraph(fn: Any) -> str | None:
    """docstring 首段 (首个空行之前, 不含 Args 节), 用作工具 description."""
    doc = inspect.getdoc(fn)
    if not doc:
        return None
    text = doc.split("Args:", 1)[0].strip()
    if not text:
        return None
    return text.split("\n\n", 1)[0].strip() or None
