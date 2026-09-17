"""prompt 包的错误族: 找不到提示词文件 / 占位符填不上值.

为什么单独一个模块: 与 db / checkpoint / retry 同一条口径 —— 每包一个基类,
调用方一次 `except PromptError` 就能兜住这一包的全部失败, 不必认识每个子类.

基类名带 `Prompt` 前缀是必须的: `model/utils/errors.py` 已经有一个
`ModelError`, 两者同名不同类 —— 那样 `except` 看起来没问题却兜不住
(`db/errors.py` 记过这条教训, 2026-09-14 实际踩过).
"""

from __future__ import annotations

from pathlib import Path


class PromptError(Exception):
    """prompt 包错误基类 (调用方一次 except 就能兜住这一包)."""


class PromptNotFoundError(PromptError):
    """提示词文件不存在 (名字写错 / 文件还没建 / 模板目录被挪走).

    attributes:
        name: 请求的提示词名 (不含 `.prompt` 后缀).
        path: 实际去找的那个文件 —— 存**绝对路径**, 报错时一眼看出找的是哪儿,
            而不是只知道自己传错了名字.
    """

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        super().__init__(f"提示词文件不存在: {path} (提示词名 {name!r})")


class PromptVariableError(PromptError):
    """提示词的占位符与传入的变量对不上.

    两种触发情形 (都由 `string.Template` 抛出、在 loader 里翻译过来):
    - 模板里有 `${foo}` 但没给 `foo` 的值 (Template 抛 KeyError);
    - 占位符写法本身不合法, 如裸 `$` 后跟非标识符字符 (Template 抛 ValueError).

    attributes:
        name: 提示词名 (不含 `.prompt` 后缀).
        detail: 对不上的具体原因 —— 缺哪个变量、已给了哪些, 或哪处写法不合法.
    """

    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail
        super().__init__(f"提示词 {name!r} 渲染失败: {detail}")
