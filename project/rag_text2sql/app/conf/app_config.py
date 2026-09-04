from dataclasses import dataclass
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


@dataclass
class AppConfig:
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig


# 配置文件路径
config_file = Path(__file__).parents[2] / "conf" / "app_config.yaml"

# 获取配置文件的数据 - 字段值
context = OmegaConf.load(config_file)

# 加载配置文件的结构
schema = OmegaConf.structured(AppConfig)

# 数据 + 结构 = 配置对象
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))
