"""
工具模块, 负责提供 escape milvus string 相关的辅助能力.
"""

from collections.abc import Iterable

# ===================== 核心辅助函数 =====================


def build_in_expr(field: str, values: Iterable[str]) -> str:
    """
    构造 Milvus 的 `field in [...]` 过滤表达式.

    比原来的 `f"{field} in {values}"` 好在**明确**: 引号用哪种、值怎么转义,
    都由这里定死, 不再依赖 `repr()` 替你挑引号的启发式.

    **要如实说的**: 这不是「修了一个已复现的 bug」。实测 (2026-09-30, 对活着的
    Milvus 逐条试) —— 单引号与双引号 Milvus **都收**; 拿带单引号 / 双引号 / 换行 /
    反斜杠的值去跑原写法, **也没试出一个被拒的**。原因是 Python 的 list repr 恰好
    吐出了 Milvus 能解析的形状 (串里含 `'` 就改用 `"` 包, 换行输出成 `\\n`)。
    所以这条的性质是**防御性加固**: 值来自文档标题 (外部内容), 而 repr 的引号策略
    是「碰巧能用」而不是契约 —— 哪天它挑出 Milvus 不认的组合, 出错方式会是
    **静默匹配不到** (而不是报错), 那更难查。逐值转义 + 固定引号, 把这份「碰巧」
    换成一条写下来的规则。

    逐值走 `escape_milvus_string` 转义, 再统一用双引号拼.

    Args:
        field: 字段名 (如 `item_name`)
        values: 取值列表
    Returns:
        str: 形如 `item_name in ["a", "b"]` 的表达式
    """
    quoted = ", ".join(f'"{escape_milvus_string(value)}"' for value in values)
    return f"{field} in [{quoted}]"


def escape_milvus_string(value: str) -> str:
    """
    Milvus 过滤表达式专用字符串安全转义函数
    核心作用:
        避免因原始字符串含特殊字符,
        导致 Milvus 解析 filter_expr 时报错,
        保证 CRUD 操作正常执行.
    转义规则:
        1. 反斜杠(\\) → 双反斜杠(\\): Milvus 表达式转义规则
        2. 双引号(") → 转义双引号(\"): 避免截断字符串表达式
        3. 换行/回车/制表符 → 空格: 防止表达式换行导致解析失败

    Args:
        value: 需要转义的原始字符串(如商品名称, 文件标题)
    Returns:
        str: 转义后的安全字符串, 可直接用于 Milvus 的 filter_expr
    """
    if value is None:
        return ""

    # 确保输入为字符串类型, 避免非字符串值报错
    s = str(value)
    # 按 Milvus 规则转义特殊字符
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    # 替换换行/回车/制表符为空格, 保证表达式单行有效
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return s
