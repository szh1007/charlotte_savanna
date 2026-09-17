"""提示词的加载与渲染: 本包唯一的读盘入口, 外加填模板要用的取数函数.

一句话理解: 提示词不是代码, 是**数据文件** —— 本模块把
`templates/{name}.prompt` 读成字符串, 并把 `${var}` 占位符填上值; 填进去的值从
哪儿来, 由本模块的取数函数负责 (现在只有一个: 实际生效的模型名).

为什么把提示词从代码里搬出来: 改一句话术不该是一次代码提交 + 重启, 而且同一段
说明被两个组件各写一遍时, 措辞必然会漂. 集中放、按名字取, 才有唯一的措辞来源.

**占位符用 `string.Template` 的 `${var}`, 不用 `str.format` 的 `{var}`** —— 这是
本模块唯一需要解释的选择:

    提示词里经常要放**字面 JSON**(要求模型按契约输出), 里面有大量 `{` `}`.
    `str.format` 会把它们当成占位符, 于是要么报错、要么被静默吞掉. 本仓两个
    先例正好一正一反: `project/charplot` 的提示词混着字面 JSON 与占位符, 靠
    「JSON 示例里恰好不出现裸 `{var}` 形态」侥幸绕开; `project/rag_knowledge`
    的 loader docstring 写明它改用 Template 就是为了规避这个. 这里直接取安全
    的那个, 不赌运气.

**不做缓存**: 系统提示词一个会话只读一次, 性能不是问题; 而缓存会带来「改了文件
不生效」的调试陷阱, 还要额外暴露一个清缓存的接口. 真需要时加 `lru_cache` 是一行
的事.
"""

from __future__ import annotations

import os
from pathlib import Path
from string import Template

from CharAgent.model.utils.config import DEFAULT_MODEL, strip_provider_prefix
from CharAgent.prompt.errors import PromptNotFoundError, PromptVariableError

# 模型名的环境变量 (与 `model/client_httpx.py` / `client_sdk.py` 读的是同一个).
# 声明成常量而不是散在函数里的字面量: 这是本包唯一读的环境变量, 审计「谁读了 env」
# 时应该一眼看得见.
ENV_MODEL_NAME = "DEEPSEEK_MODEL_NAME"

# 提示词文件目录 (与代码同包, 路径相对本文件定位, 与 cwd 无关).
#
# 为什么用 pathlib 相对包路径而不是 importlib.resources: 本仓既有的静态文件定位
# 法就是这个 (tests/mock_llm.py 的 SAMPLE_DIR, client/app.py 的 _ROOT_ENV), 且
# pyproject.toml 没有打包配置 (只有 [tool.ruff]), 不存在「数据文件要声明进
# package-data」的约束 —— 引入 resources 只会多一套没人用的机制.
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# 提示词文件后缀 (与 rag_knowledge / rag_text2sql 的既有约定一致: 调用方只给名字)
_SUFFIX = ".prompt"


def load_prompt(name: str, **values: str) -> str:
    """加载提示词并渲染占位符.

    Args:
        name: 提示词名 (不带 `.prompt` 后缀), 对应 `templates/{name}.prompt`.
        **values: 填给 `${var}` 占位符的值. 文件里没有占位符时可以不传.

    Returns:
        str: 渲染好的提示词正文 (文件里的换行原样保留).

    Raises:
        PromptNotFoundError: 文件不存在 (报错带绝对路径, 便于定位找的是哪儿).
        PromptVariableError: 占位符缺值, 或占位符写法不合法.
    """
    path = TEMPLATE_DIR / f"{name}{_SUFFIX}"
    if not path.is_file():
        raise PromptNotFoundError(name, path)

    raw = path.read_text(encoding="utf-8")
    try:
        return Template(raw).substitute(values)
    except KeyError as exc:
        # Template 用 KeyError 报「模板里有这个占位符但没给值」, 缺的名字在 args[0]
        given = ", ".join(sorted(values)) if values else "(一个都没给)"
        raise PromptVariableError(
            name,
            f"模板里有 {exc.args[0]!r} 这个占位符, 但没给它的值; 已给: {given}",
        ) from exc
    except ValueError as exc:
        # 形如 "$5" / "$ 中文" 的写法不合法 —— Template 抛 ValueError
        raise PromptVariableError(name, f"占位符写法不合法: {exc}") from exc


def resolve_model_name(model_name: str | None = None) -> str:
    """解析本次运行实际使用的模型名 (与发给 API 的那个逐字一致).

    放在本包是因为它是**填提示词用的取数函数**: `system.prompt` 里那句
    「你的底层模型是 ${model_name}」的值就是从这儿来的.

    规则必须跟 ChatModel 适配器 (`model/client_httpx.py` / `client_sdk.py`)
    **逐步**对齐 —— 两步, 少一步就与实际的模型名对不上:

    1. **取值**: CLI 显式指定 (--model) 优先 → 否则读 .env 的
       DEEPSEEK_MODEL_NAME → 再否则用默认值;
    2. **剥掉 provider 前缀**: 根 .env 的模型名是 LangChain demo 共享的
       `deepseek:deepseek-flash`, 而裸调端点只接受裸名 —— 两个适配器都拿
       `strip_provider_prefix` 过一遍. 少了这一步, prompt 会说
       `deepseek:deepseek-flash`: 一个 API 从来没见过、也说不出口的名字.

    不一致的后果是 prompt 在身份上撒谎, 比不写更糟 —— 这段 prompt 唯一的职责
    就是照实说明身份.

    Args:
        model_name: CLI 传来的模型名; None 表示听 .env 的.

    Returns:
        str: 实际生效的模型名 (已剥前缀, 与发给 API 的一致).
    """
    resolved = model_name or os.environ.get(ENV_MODEL_NAME) or DEFAULT_MODEL
    return strip_provider_prefix(resolved)
