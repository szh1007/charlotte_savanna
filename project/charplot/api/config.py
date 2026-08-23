"""CharPlot FastAPI 侧环境配置集中读取 (Issue 03, 07).

.env 加载在此模块顶部完成 (首个被 import 的配置模块), 保证 config 的
模块级读取 (INTERNAL_TOKEN / DJANGO_API_BASE / LLM / 检索源配置) 拿到真实值.
路径显式指向项目根 .env (parents[3] = charlotte_savanna, 含 DEEPSEEK /
TAVILY 等共享配置), 不依赖进程 cwd; 测试通过 conftest 在 import 前直接
设置环境变量, 覆盖 .env.
"""

import os
from pathlib import Path

import dotenv

dotenv.load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# 调 Django 内部端点 (图谱落库/失败标记/取文件内容) 的共享 token,
# 未配置时 Django 侧拒绝
INTERNAL_TOKEN = os.environ.get("CHARPLOT_INTERNAL_TOKEN", "")
# Django 业务侧基础 URL (FastAPI → Django 内部端点)
DJANGO_API_BASE = os.environ.get(
    "CHARPLOT_DJANGO_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")

# ---- LLM 与检索源 (Issue 07 真实管道, 与 deep_search 共用根 .env) ----
# DeepSeek 模型名 (init_chat_model 格式: deepseek:xxx), 未配置时管道不可用
LLM_MODEL = os.environ.get("DEEPSEEK_MODEL_NAME", "")
# Tavily 网络搜索 key, 未配置时跳过网络检索源 (降级, 其余源不受影响)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
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

# ---- RAG 全链路 (Issue 10, rag/ 模块) ----
# Milvus 向量库地址 (与 deep_search 共用实例; 未配置时索引/检索不可用)
MILVUS_URL = os.environ.get("MILVUS_URL", "http://localhost:19530")
# Embedding 模型接入 (可切换抽象, rag/embeddings.py 工厂): 当前仅
# bge-m3 (pymilvus BGEM3EmbeddingFunction, 本地模型稠密+稀疏一次出),
# 新增模型实现 Embedder 协议并在 get_embedder 注册
EMBEDDING_MODEL = os.environ.get("CHARPLOT_EMBEDDING_MODEL", "bge-m3")
# bge-m3 本地模型路径/名称 (HuggingFace 名或本地目录), 设备与 fp16 加速
EMBEDDING_MODEL_NAME = os.environ.get("CHARPLOT_EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
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
# (FlagReranker), 配置留空 = 降级不重排 (warning, 模型下载为主动行为)
RERANKER_MODEL = os.environ.get("CHARPLOT_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
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
