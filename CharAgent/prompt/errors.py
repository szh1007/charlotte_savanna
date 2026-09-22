"""prompt 包的错误族: 找不到提示词文件 / 占位符填不上值 / 引用与正文对不上.

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


class PromptRefMismatchError(PromptError):
    """身份说明的**引用**与手里那份正文对不上 (引用陈旧 / 拿错了历史).

    触发点只有写帧那一侧 (`ref.detach_identity`): 调用方手上有一个引用, 也有一段
    历史, 但两者说的不是同一份正文. 这是一种**装配 bug**, 不是数据问题 —— 引用在
    会话装配时算好之后就不该再变, 而历史若是从别处来的 (另一个会话 / 手拼的),
    它第 0 条自然不是那条身份说明.

    为什么必须报错而不是挑一个用: 照这个引用把帧存下去, 这段会话从此每一帧都记着
    一个**错的**身份说明; 而读的人只会看到「名字 + 哈希」, 没有任何线索知道它错了.

    attributes:
        name: 引用里的提示词名.
        expected: 引用里记的 sha256 (前 12 位即可定位).
        actual: 手里那条正文算出来的 sha256.
    """

    def __init__(self, name: str, expected: str, actual: str) -> None:
        self.name = name
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"身份说明的引用与正文对不上 (提示词 {name!r}: 引用记的是 "
            f"{expected[:12]}, 手里这条算出来是 {actual[:12]}): 引用在装配时算好, "
            "之后一个字都不该变 —— 检查这段历史是不是从别处拿来的"
        )


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
