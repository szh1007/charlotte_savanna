"""
LLM 配置模块, 负责读取对话模型与视觉模型相关环境变量.
"""

from dataclasses import dataclass

from .common import env_float, env_str


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    lv_model: str
    llm_model: str
    llm_temperature: float


llm_config = LLMConfig(
    base_url=env_str("RK_DEEPSEEK_BASE_URL"),
    api_key=env_str("RK_DEEPSEEK_API_KEY"),
    lv_model=env_str("RK_VL_MODEL"),
    llm_model=env_str("RK_LLM_DEFAULT_MODEL"),
    llm_temperature=env_float("RK_LLM_DEFAULT_TEMPERATURE"),
)
