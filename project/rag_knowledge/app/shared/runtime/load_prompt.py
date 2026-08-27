"""
加载并渲染 prompt 模板.

支持两种占位符风格(向后兼容):
1. string.Template 风格: $var / ${var}
2. str.format 旧风格: {var} / {var[0]}(索引访问)

相比 str.format, 改用 string.Template 的好处:
- prompt 中出现字面量 { }(如要求模型输出 JSON 的示例)不会被误解析成占位符
- 占位符语义更明确, 缺参时能精准定位变量名
"""

import re
from string import Template

from .logger import PROJECT_ROOT, logger

# 匹配 {name} 或 {name[0]} / {name['key']} 风格的旧占位符
# 负向 lookbehind 排除 ${name} 中的 {name}, 避免与 Template 占位符冲突
_BRACED_FIELD = re.compile(r"(?<!\$)\{([A-Za-z_]\w*)(?:\[([^\]]+)\])?\}")


def _to_str(value) -> str:
    """把渲染值安全转为字符串, 兼容 None / 元组 / 列表等非字符串场景."""
    if value is None:
        return ""
    return str(value)


def _resolve_braced(match, kwargs: dict) -> str:
    """把 {name} / {name[index]} 旧风格占位符转换为可渲染的值."""
    name = match.group(1)
    index_expr = match.group(2)

    if name not in kwargs:
        raise KeyError(f"提示词占位符变量未提供: {name}")

    value = kwargs[name]

    # 无索引: 转成 ${name}, 统一交给 Template 渲染(保持值转换逻辑一致)
    if index_expr is None:
        return "${" + name + "}"

    # 有索引: 直接求值替换(如 {image_content[0]}), 兼容 str.format 的索引访问
    # 值会直接插入模板, 需转义 $ / { / }, 避免被 Template 渲染或 {{ 还原误解析
    # (PDF 等来源的上下文文本可能含 $ / 花括号)
    idx = index_expr.strip().strip("'\"")
    try:
        resolved = value[int(idx)] if idx.isdigit() else value[idx]
    except (TypeError, KeyError, IndexError, ValueError) as e:
        raise KeyError(f"占位符 {{{name}[{idx}]}} 解析失败: {e}") from e
    escaped = _to_str(resolved).replace("$", "$$").replace("{", "{{").replace("}", "}}")
    return escaped


def load_prompt(name: str, **kwargs) -> str:
    """
    加载提示词并渲染变量占位符.

    Args:
        name: 提示词文件名(不带 .prompt 后缀, 如 image_summary).
        **kwargs: 需渲染的变量键值对,
                    如 root_folder="..." / image_content=("...", "...").

    Returns:
        str: 渲染后的最终提示词字符串.

    Raises:
        FileNotFoundError: 提示词文件不存在.
        KeyError: 模板中存在未提供的占位符变量.
    """
    # 1. 拼接提示词路径
    prompt_path = PROJECT_ROOT / "app" / "prompts" / f"{name}.prompt"

    # 2. 校验文件是否存在, 提前抛出明确异常
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_path.absolute()}")

    # 3. 读取纯文本提示词
    raw_prompt = prompt_path.read_text(encoding="utf-8")

    # 4. 无变量时直接返回原文本
    if not kwargs:
        return raw_prompt

    # 5. 兼容 {var} 旧风格 → 转成 ${var} / 索引直接求值
    raw_prompt = _BRACED_FIELD.sub(lambda m: _resolve_braced(m, kwargs), raw_prompt)
    # JSON 字面大括号: {{ → {, }} → }
    raw_prompt = raw_prompt.replace("{{", "{").replace("}}", "}")

    # 6. 用 string.Template 渲染 ${var} / $var, 值统一转字符串
    rendered_prompt = Template(raw_prompt).substitute(
        {key: _to_str(value) for key, value in kwargs.items()}
    )
    logger.debug(f"提示词渲染成功, 替换变量: {list(kwargs.keys())}")
    return rendered_prompt


if __name__ == "__main__":
    result = load_prompt(
        name="image_summary", root_folder="images", image_content=("x", "y")
    )
    print(result)
