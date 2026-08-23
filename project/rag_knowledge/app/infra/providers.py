import copy
from dataclasses import dataclass, field

from ..shared.config.bailian_mcp_config import McpConfig, mcp_config
from ..shared.config.embedding_config import EmbeddingConfig, embedding_config
from ..shared.config.llm_config import LLMConfig, llm_config
from ..shared.config.milvus_config import MilvusConfig, milvus_config
from ..shared.config.mineru_config import MinerUConfig, mineru_config
from ..shared.config.minio_config import MinIOConfig, minio_config
from ..shared.config.reranker_config import RerankerConfig, reranker_config
from ..shared.config.settings_config import AppSettings, settings

"""
创建一个实体类
    方式1: InfraConfig  -> @dataclass
                        -> name, age
                        -> InfraConfig(name="xx", age=xx)
                        -> __init__(slf, name, age)
        本质就是普通的类, 添加了一些方便初始化的方法, 例如: __init__
        default_factory + copy.deepcopy -> 防止在内存中指向同一个地址

    方式2: InfraConfig  -> BaseModel
                        -> name, age
                        -> InfraConfig(name="xx", age=xx)
                        -> __init__(slf, name, age)
        继承子类 + 添加了一些方便初始化的方法,
        例如: 1.__init__ 2.添加了参数校验 3.更方便进行json的序列化(dump)

    方式3: InfraConfig -> TypedDict
        Langgraph的专属 -> state -> TypedDict -> node return
"""


@dataclass
class InfraConfig:
    embedding_config: EmbeddingConfig = field(
        default_factory=lambda: copy.deepcopy(embedding_config)
    )
    llm_config: LLMConfig = field(default_factory=lambda: copy.deepcopy(llm_config))
    mcp_config: McpConfig = field(default_factory=lambda: copy.deepcopy(mcp_config))
    milvus_config: MilvusConfig = field(
        default_factory=lambda: copy.deepcopy(milvus_config)
    )
    mineru_config: MinerUConfig = field(
        default_factory=lambda: copy.deepcopy(mineru_config)
    )
    minio_config: MinIOConfig = field(
        default_factory=lambda: copy.deepcopy(minio_config)
    )
    reranker_config: RerankerConfig = field(
        default_factory=lambda: copy.deepcopy(reranker_config)
    )
    settings: AppSettings = field(default_factory=lambda: copy.deepcopy(settings))


infra_config = InfraConfig()
print(infra_config.llm_config.api_key)
