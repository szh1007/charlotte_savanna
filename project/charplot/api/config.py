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
