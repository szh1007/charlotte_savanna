from dataclasses import dataclass, field
from pathlib import Path

from omegaconf import OmegaConf


# 日志配置
@dataclass
class File:
    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    enable: bool
    level: str


@dataclass
class LoggingConfig:
    file: File
    console: Console


# 数据库配置
@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    host: str
    port: int
    embedding_size: int
    collection_name_column: str
    collection_name_metric: str


@dataclass
class EmbeddingConfig:
    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    host: str
    port: int
    index_name: str


@dataclass
class LLMConfig:
    model_name: str
    api_key: str
    # C16: LLM 调用的预算 —— 没有它时 SDK 默认读超时 600s, 一次上游挂起
    # 能烧掉 10 分钟 (C15 跑批实测: 2/117 次卡在取值召回的调用上)
    timeout_s: float = 60.0
    # 重试次数交给 OpenAI SDK: 它只重瞬态 (429 / 5xx / 连接失败 / 超时),
    # 4xx 直接放弃 —— 与 C16 票面的判据一致, 不自己再包一层
    max_retries: int = 2


@dataclass
class DwGuardConfig:
    """dw 连接的加固档位 (C16): 语句预算 + 两层传输超时."""

    statement_timeout_ms: int = 10_000
    read_timeout_s: int = 30
    connect_timeout_s: int = 5


@dataclass
class RecallConfig:
    """三路召回节点内部的并发档位 (C18).

    每个关键词要发一次检索; 串行时 N 个关键词就是 N 个 RTT 相加. 上限是给
    嵌入服务 / Qdrant / ES 的连接池留的余量 —— 关键词个数由 LLM 扩展决定
    (实测 2~10 个), 不设上限等于把并发交给上游模型的心情.
    """

    concurrency: int = 4


@dataclass
class AppConfig:
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    # 有默认值: 老配置文件缺这一节也能起 (合并时按默认补齐)
    dw_guard: DwGuardConfig = field(default_factory=DwGuardConfig)
    recall: RecallConfig = field(default_factory=RecallConfig)


# 配置文件路径
config_file = Path(__file__).parents[2] / "conf" / "app_config.yaml"

# 获取配置文件的数据 - 字段值
context = OmegaConf.load(config_file)

# 加载配置文件的结构
schema = OmegaConf.structured(AppConfig)

# 数据 + 结构 = 配置对象
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))
