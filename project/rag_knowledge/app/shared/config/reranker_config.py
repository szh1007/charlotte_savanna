"""
Reranker 配置模块, 负责读取重排模型相关环境变量.
"""

from dataclasses import dataclass

from .common import env_bool, env_str


@dataclass
class RerankerConfig:
    bge_reranker_large: str
    bge_reranker_device: str
    bge_reranker_fp16: bool


reranker_config = RerankerConfig(
    bge_reranker_large=env_str("RK_BGE_RERANKER_LARGE"),
    bge_reranker_device=env_str("RK_BGE_RERANKER_DEVICE"),
    bge_reranker_fp16=env_bool("RK_BGE_RERANKER_FP16"),
)
