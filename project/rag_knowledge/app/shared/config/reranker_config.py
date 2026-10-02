"""
Reranker 配置模块, 负责读取重排模型相关环境变量.
"""

from dataclasses import dataclass

from .common import env_bool, env_int, env_str


@dataclass
class RerankerConfig:
    # 变量名里的 "large" 是历史原因 (最早接的是 bge-reranker-large), 现在它只是
    # 「重排模型的本地路径」—— 换模型改 .env 的值即可, 不用动代码.
    bge_reranker_large: str
    bge_reranker_device: str
    bge_reranker_fp16: bool
    # 重排模型的最大输入长度 (token). FlagReranker 默认 512, 而中文 chunk 动辄
    # 600~1200 token —— 保持默认就会把正文截掉一半, 也让上层不得不先做一次
    # LLM 压缩来迁就窗口. 默认给 2048: 当前语料最长 chunk 约 1250 token,
    # 留了余量, 又比 8192 快得多 (attention 是平方复杂度).
    bge_reranker_max_length: int


reranker_config = RerankerConfig(
    bge_reranker_large=env_str("RK_BGE_RERANKER_LARGE"),
    bge_reranker_device=env_str("RK_BGE_RERANKER_DEVICE"),
    bge_reranker_fp16=env_bool("RK_BGE_RERANKER_FP16"),
    bge_reranker_max_length=env_int("RK_BGE_RERANKER_MAX_LENGTH", 2048),
)
