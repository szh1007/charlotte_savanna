"""面向模型的错误文案生成 (difficulties #2): 校验失败 → 可操作中文文本.

executor 拆出的独立纯函数块 (方案 B): pydantic ValidationError 逐条映射为
「字段路径 + 期望 + 实际」的可操作信息, 供 agent loop 错误自纠错回填模型;
不甩原始 traceback. 未来结构化输出校验 (P1) / 上下文工程可复用同一映射.

文案构成:
- 缺必填 / 类型错误 (期望类型 hint 表) / 枚举越界 (允许值展开) / pattern /
  数值界值 (gt/ge/lt/le 措辞区分) / 长度约束
- 多错误逐行拼接, 超 _MAX_ERRORS 条截断 (上下文预算)
- 意外异常的统一内部文案 (不诱导模型用相同参数重试)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ValidationError

# 意外异常回填模型的通用文案: 不泄露 traceback, 不诱导模型用相同参数重试
INTERNAL_ERROR_TEXT = (
    "工具执行时发生内部错误 (通常为数据/服务故障, 而非参数问题). "
    "请勿使用相同参数重试; 可调整参数或告知用户服务暂时不可用."
)

# 校验错误给模型的提示条数上限 (过长浪费上下文)
_MAX_ERRORS = 5

# ValidationError type → 期望类型提示 (常见类型错误, 其余走 pydantic msg 兜底)
_EXPECTED_HINT: dict[str, str] = {
    "string_type": "字符串 (str)",
    "int_parsing": "整数 (int)",
    "int_from_float": "整数 (int)",
    "float_parsing": "数值 (float)",
    "bool_parsing": "布尔 (bool)",
    "list_type": "数组 (list)",
    "dict_type": "对象 (dict)",
}

# 数值越界错误 → (措辞, ctx 界值键): gt 用「必须大于」, ge 用「不能小于」
_NUMERIC_BOUNDS: dict[str, tuple[str, str]] = {
    "less_than": ("必须小于", "lt"),
    "less_than_equal": ("不能大于", "le"),
    "greater_than": ("必须大于", "gt"),
    "greater_than_equal": ("不能小于", "ge"),
}


def _error_type_message(error_type: str) -> str | None:
    """ValidationError type 中缀 → 中文期望说明 (None 表示走通用兜底)."""
    if error_type in ("literal_error", "enum", "int_enum"):
        return "取值必须在允许列表中"
    if error_type == "string_pattern_mismatch":
        return "格式不符合要求"
    if error_type == "string_too_short":
        return "字符串过短, 不满足最小长度"
    if error_type == "string_too_long":
        return "字符串过长, 超过最大长度"
    if error_type == "extra_forbidden":
        return "不允许出现该字段"
    return None


def _describe_validation_error(error: dict[str, Any]) -> str:
    """单条 pydantic 校验错误 → 「字段路径 + 期望 + 实际」中文文本 (#2)."""
    loc = error.get("loc", ())
    path = ".".join(str(part) for part in loc) if loc else "参数对象"
    ctx = error.get("ctx") or {}
    input_value = error.get("input")
    error_type = str(error.get("type", ""))

    if error_type == "missing":
        return f"缺少必填参数 {path}"

    if error_type in _NUMERIC_BOUNDS:
        word, ctx_key = _NUMERIC_BOUNDS[error_type]
        bound = ctx.get(ctx_key)
        suffix = f" {bound}" if bound is not None else ""
        return f"参数 {path} 数值{word}{suffix}, 实际: {input_value!r}"

    expectation = _error_type_message(error_type)
    if expectation is None:
        hint = _EXPECTED_HINT.get(error_type)
        if hint:
            return f"参数 {path} 类型错误: 期望 {hint}, 实际: {input_value!r}"
        message = str(error.get("msg", "")).replace("\n", " ")[:120]
        return f"参数 {path} 期望: {message}, 实际: {input_value!r}"

    parts = [f"参数 {path}", expectation]
    if "pattern" in ctx:
        parts.append(f"需匹配 {ctx['pattern']}")
    if "min_length" in ctx:
        parts.append(f"最短 {ctx['min_length']} 字符")
    if "max_length" in ctx:
        parts.append(f"最长 {ctx['max_length']} 字符")
    if "expected" in ctx:
        allowed = ctx["expected"]
        # StrEnum 字段的 ctx.expected 是枚举类, 展开为成员值便于模型理解
        if isinstance(allowed, type) and issubclass(allowed, Enum):
            allowed = [member.value for member in allowed]
        parts.append(f"允许值 {allowed}")
    parts.append(f"实际: {input_value!r}")
    return " ".join(parts)


def validation_error_text(exc: ValidationError) -> str:
    """整条 ValidationError → 可操作文本 (多条逐行, 超出截断)."""
    lines = [_describe_validation_error(e) for e in exc.errors()]
    if len(lines) > _MAX_ERRORS:
        lines = [*lines[:_MAX_ERRORS], f"... 共 {len(lines)} 处错误"]
    return ";\n".join(lines)
