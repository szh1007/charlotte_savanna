"""LLM 模型单例 (Issue 07).

与 deep_search 同款初始化: DeepSeek 模型 + 关闭 thinking 加速.
惰性单例: 首次调用时构建 (避免 import 阶段初始化客户端, 测试可
monkeypatch get_chat_model 返回假模型隔离外部依赖).
"""

from langchain.chat_models import init_chat_model

from ..api import config

_model = None


def get_chat_model():
    """惰性构建 ChatModel 单例 (DeepSeek, thinking 关闭).

    api_key / api_base 显式透传 (CHARPLOT_ 前缀配置), 不依赖 langchain
    库级 DEEPSEEK_API_KEY / DEEPSEEK_API_BASE 环境变量读取; 留空时由库
    兜底默认值 (如官方 base URL).
    """
    global _model
    if _model is None:
        if not config.LLM_MODEL:
            raise RuntimeError("未配置 CHARPLOT_DEEPSEEK_MODEL_NAME, 知识管道不可用")
        kwargs = {
            "model": config.LLM_MODEL,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if config.DEEPSEEK_API_KEY:
            kwargs["api_key"] = config.DEEPSEEK_API_KEY
        if config.DEEPSEEK_API_BASE:
            kwargs["api_base"] = config.DEEPSEEK_API_BASE
        _model = init_chat_model(**kwargs)
    return _model
