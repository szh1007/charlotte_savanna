"""prompt 包: 提示词的集中存放与按名加载 (difficulties #69).

一句话理解: 提示词从这里**按名字取**, 不写在代码里. 一个 `.prompt` 文件就是一段
提示词, 组件调 `load_prompt("system", model_name=...)` 拿渲染好的正文.

它解决什么问题: 提示词原本硬编码在组件里 (client/session.py 曾自己拼一段身份
说明). 三处不好 —— 改一句话术要改代码走一次提交, 且改动混在业务逻辑里; 没有
归属地, 以后业务提示词往哪放没有约定; 谁需要谁自己拼字符串, 同一段说明被两处
各写一遍时措辞必然漂.

业务提示词放哪儿 (归属地问题的答案): **业务自己的目录**, 由业务把目录传给
`load_prompt(..., prompt_dir=...)`. 本包 `templates/` 里现在两份, **都不是业务
提示词** —— `system.prompt` 是框架自己的身份说明 (演示用), `service.prompt` 只是一处
**占位** (框架后续引入别的功能 / 组件时可能要用到, 当前没有任何代码在读它). 真正的
业务提示词 (话术、禁则、示例) 从第一行起就待在业务目录里, 框架目录里不留 —— 这是
「框架不认识业务」这条规矩的一个具体落点.

设计依据 (CharAgent/docs):
- difficulties #69 Prompt Engineering (标 P0-P1): 提示词要能独立于代码演进
- 本包是「版本化 prompt」的落脚点: 届时按版本取文件即可, 调用方签名不用变

结构总览:

| 文件 | 管什么 |
|------|--------|
| `load.py` | 读盘入口 (读 `{prompt_dir}/{name}.prompt`, 渲染 `${var}`) + 取数函数 |
| `errors.py` | `PromptError` 族 (找不到文件 / 占位符对不上) |
| `templates/` | 提示词文件本身, 一个 `.prompt` 一段提示词 |

怎么用::

    from CharAgent.prompt import load_prompt, resolve_model_name

    system = load_prompt("system", model_name=resolve_model_name())

`system.prompt` 的内容取舍与出处: 四条工作原则 (事实靠工具取 / 如实报告失败与
不确定 / 简洁直接 / 不擅自扩大范围) 取自 Anthropic《Building Effective Agents》
对 agent 的两条论断, 见
https://www.anthropic.com/engineering/building-effective-agents ; 该文的「三条
核心原则」(simplicity / transparency / ACI) **没有**写进去 —— 那是给框架设计者
的 (怎么搭 agent), 不是给 agent 的提示词措辞, 不把出处挂到它没说过的话上.

刻意排除: `TEMPLATE_DIR` 与 `ENV_MODEL_NAME` 两个常量**不上浮**到门面 —— 它们是
本包的内部定位与配置零件, 上浮到根门面只会变成看不出归属的通名 (与 checkpoint /
db 门面不导出它们的 `ENV_*` 同一条口径). 需要的人
`from CharAgent.prompt.load import TEMPLATE_DIR` 取.

还有一条边界要写清楚 —— **框架机制文本不走这里**: `agent/utils/messages.py` 的
两个截断续写 / 精简指令 (`TRUNCATION_*_TEXT`) 同样会发给模型 (loop 把它们注入成
system 消息), 但它们是 loop 的既定行为, 不是产品可配置的提示词; 搬进来会让核心
agent 层依赖磁盘 I/O —— 快照回放时读盘失败会连累主循环. 本包只管「人设 / 业务
类」提示词.
"""

from __future__ import annotations

from CharAgent.prompt.errors import (
    PromptError,
    PromptNotFoundError,
    PromptVariableError,
)
from CharAgent.prompt.load import load_prompt, resolve_model_name

__all__ = [
    "PromptError",
    "PromptNotFoundError",
    "PromptVariableError",
    "load_prompt",
    "resolve_model_name",
]
