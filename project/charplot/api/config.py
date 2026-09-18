"""CharPlot FastAPI 侧环境配置集中读取.

.env 加载在此模块顶部完成 (首个被 import 的配置模块), 保证 config 的
模块级读取 (INTERNAL_TOKEN / DJANGO_API_BASE / LLM / 检索源配置) 拿到真实值.
**一切配置以 project/charplot/.env 为准** (模板见 .env.example), 变量统一
CHARPLOT_ 前缀, 不依赖根 .env 与进程 cwd; dotenv 默认不覆盖已存在变量
(测试通过 conftest 在 import 前直接设置环境变量覆盖 .env).
"""

import os
from pathlib import Path

import dotenv

dotenv.load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 调 Django 内部端点 (图谱落库/失败标记/取文件内容) 的共享 token,
# 未配置时 Django 侧拒绝
INTERNAL_TOKEN = os.environ.get("CHARPLOT_INTERNAL_TOKEN", "")
# Django 业务侧基础 URL (FastAPI → Django 内部端点)
DJANGO_API_BASE = os.environ.get(
    "CHARPLOT_DJANGO_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")

# ---- LLM 与检索源 (真实管道, 全部 CHARPLOT_ 前缀) ----
# DeepSeek 模型名 (init_chat_model 格式: deepseek:xxx), 未配置时管道不可用
LLM_MODEL = os.environ.get("CHARPLOT_DEEPSEEK_MODEL_NAME", "")
# DeepSeek API key / base (显式传给 ChatDeepSeek, 不依赖库级 DEEPSEEK_* 读取)
DEEPSEEK_API_KEY = os.environ.get("CHARPLOT_DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.environ.get("CHARPLOT_DEEPSEEK_API_BASE", "")
# Tavily 网络搜索 key, 未配置时跳过网络检索源 (降级, 其余源不受影响)
TAVILY_API_KEY = os.environ.get("CHARPLOT_TAVILY_API_KEY", "")
# Context7 官方文档检索 API 基础地址 (公开接口, 无需 key; v2 端点相对 /api)
CONTEXT7_BASE_URL = os.environ.get(
    "CHARPLOT_CONTEXT7_BASE_URL", "https://context7.com/api"
)
# Context7 每库查询文档数上限 (官方默认 5)
CONTEXT7_MAX_DOCS = int(os.environ.get("CHARPLOT_CONTEXT7_MAX_DOCS", "5"))
# 网页链接抓取超时 (秒)
LINK_FETCH_TIMEOUT = float(os.environ.get("CHARPLOT_LINK_FETCH_TIMEOUT", "10"))
# LLM 分析/解构失败重试次数 (每次重试带上次错误反馈给模型修正)
LLM_RETRIES = int(os.environ.get("CHARPLOT_LLM_RETRIES", "1"))

# ---- RAG 全链路 (rag/ 模块) ----
# modelscope 本地模型根目录 (modelscope 下载平铺结构: {root}/models/{org}/{name}).
# 模型加载前经 resolve_local_model_path 解析到本地目录, 本地缺失时报错
# (embedding) / 降级 (rerank), 杜绝库静默从 HF 重新下载 (bge-m3 约 2GB)
MODELSCOPE_ROOT = os.environ.get(
    "CHARPLOT_MODELSCOPE_ROOT", r"D:/__WorkSpace__/modelscope"
)
# Milvus 向量库地址 (与 deep_search 共用实例, 值相同但变量名独立;
# 未配置时索引/检索不可用)
MILVUS_URL = os.environ.get("CHARPLOT_MILVUS_URL", "http://localhost:19530")
# Embedding 模型接入 (可切换抽象, rag/embeddings.py 工厂): 当前仅
# bge-m3 (pymilvus BGEM3EmbeddingFunction, 本地模型稠密+稀疏一次出),
# 新增模型实现 Embedder 协议并在 get_embedder 注册
EMBEDDING_MODEL = os.environ.get("CHARPLOT_EMBEDDING_MODEL", "bge-m3")
# bge-m3 本地模型路径 (modelscope 下载目录, 或 "BAAI/bge-m3" 风格引用 →
# 加载前自动解析 MODELSCOPE_ROOT/models/BAAI/bge-m3), 设备与 fp16 加速
EMBEDDING_MODEL_NAME = os.environ.get(
    "CHARPLOT_EMBEDDING_MODEL_NAME",
    str(Path(MODELSCOPE_ROOT) / "models" / "BAAI" / "bge-m3"),
)
EMBEDDING_DEVICE = os.environ.get("CHARPLOT_EMBEDDING_DEVICE", "cpu")
EMBEDDING_FP16 = os.environ.get("CHARPLOT_EMBEDDING_FP16", "false").lower() in (
    "1",
    "true",
    "yes",
)
# bge-m3 稠密向量维度 (collection schema 与查询向量维度校验用)
EMBEDDING_DIM = int(os.environ.get("CHARPLOT_EMBEDDING_DIM", "1024"))
# 文档切分参数 (按文档类型调优, rag/chunking.py; 默认 md/txt 档)
CHUNK_SIZE = int(os.environ.get("CHARPLOT_CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("CHARPLOT_CHUNK_OVERLAP", "50"))
# Rerank 模型 (必配链路, 抽象可切换): 本地 bge-reranker-v2-m3
# (FlagReranker), 值为 modelscope 本地路径 / HF 风格引用 (经 resolve_local_model_path
# 解析); 本地未找到 = 降级 Noop 不精排 (warning 日志明示原因), 不触发自动下载
RERANKER_MODEL = os.environ.get(
    "CHARPLOT_RERANKER_MODEL",
    str(Path(MODELSCOPE_ROOT) / "models" / "BAAI" / "bge-reranker-v2-m3"),
)
RERANKER_DEVICE = os.environ.get("CHARPLOT_RERANKER_DEVICE", "cpu")
RERANKER_FP16 = os.environ.get("CHARPLOT_RERANKER_FP16", "false").lower() in (
    "1",
    "true",
    "yes",
)
# 检索参数: 混合召回量 (精排前) 与精排后 Top-K
RETRIEVE_TOP_K = int(os.environ.get("CHARPLOT_RETRIEVE_TOP_K", "20"))
RERANK_TOP_K = int(os.environ.get("CHARPLOT_RERANK_TOP_K", "5"))
# Query rewriting: 检索前 LLM 改写 (rewrite 失败自动降级原 query, 不阻塞)
QUERY_REWRITE = os.environ.get("CHARPLOT_QUERY_REWRITE", "true").lower() in (
    "1",
    "true",
    "yes",
)


def resolve_local_model_path(model_ref: str) -> str | None:
    """把模型引用解析为**已存在**的本地模型目录 (含 config.json), 否则 None.

    解析顺序:
    1. 引用本身即本地目录 (绝对/相对路径, 如 .env 里显式配置的 modelscope 路径)
    2. `org/name` 风格引用 (HF / modelscope 命名, 如 "BAAI/bge-m3") →
       拼接 MODELSCOPE_ROOT/models/{org}/{name} 平铺布局

    返回 None 表示本地不存在 → 调用方据此报错 (embedding) 或降级 (rerank),
    不触发库级自动下载.
    """
    ref = Path(model_ref.strip())
    if ref.is_dir() and (ref / "config.json").is_file():
        return str(ref)
    if len(ref.parts) == 2:  # org/name 风格引用
        candidate = Path(MODELSCOPE_ROOT) / "models" / ref.parts[0] / ref.parts[1]
        if (candidate / "config.json").is_file():
            return str(candidate)
    return None
