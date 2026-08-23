"""LLM 模型单例 (Issue 07).

与 deep_search 同款初始化: DeepSeek 模型 + 关闭 thinking 加速.
惰性单例: 首次调用时构建 (避免 import 阶段初始化客户端, 测试可
monkeypatch get_chat_model 返回假模型隔离外部依赖).
"""

from langchain.chat_models import init_chat_model

from ..api import config

_model = None


def get_chat_model():
    """惰性构建 ChatModel 单例 (DeepSeek, thinking 关闭)."""
    global _model
    if _model is None:
        if not config.LLM_MODEL:
            raise RuntimeError("未配置 DEEPSEEK_MODEL_NAME, 知识管道不可用")
        _model = init_chat_model(
            model=config.LLM_MODEL,
            extra_body={"thinking": {"type": "disabled"}},
        )
    return _model
