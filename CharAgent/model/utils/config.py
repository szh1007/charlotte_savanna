"""适配器共享配置: DeepSeek 默认端点与模型名规范化 (双适配器共用).

两个适配器 (client_httpx.py 裸调 / client_sdk.py openai SDK) 指向同一组默认值与模型名
处理规则, 收编于此避免常量与函数挂在任一适配器上 (兄弟模块反向依赖会
破坏「并列实现」的模块边界).
"""

from __future__ import annotations

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def strip_provider_prefix(model: str) -> str:
    """剥离 LangChain 风格 provider:model 前缀 (如 deepseek:deepseek-v4-flash).

    根 .env.example 的 DEEPSEEK_MODEL_NAME 为 LangChain demo 共享, 裸调端点只接受裸名.
    """
    if ":" in model:
        _, _, name = model.partition(":")
        return name
    return model
