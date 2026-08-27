"""
Milvus 配置模块, 负责读取向量库相关环境变量.
"""

from dataclasses import dataclass

from .common import env_int, env_str


@dataclass
class MilvusConfig:
    dim: int
    milvus_url: str
    chunks_collection: str
    item_name_collection: str


milvus_config = MilvusConfig(
    milvus_url=env_str("RK_MILVUS_URL"),
    chunks_collection=env_str("RK_CHUNKS_COLLECTION"),
    item_name_collection=env_str("RK_ITEM_NAME_COLLECTION"),
    dim=env_int("RK_EMBEDDING_DIM"),
)
