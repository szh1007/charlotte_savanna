"""CharPlot FastAPI 侧环境配置集中读取 (Issue 03).

.env 加载在此模块顶部完成 (首个被 import 的配置模块), 保证 config 的
模块级读取 (INTERNAL_TOKEN / DJANGO_API_BASE / STUB_DELAY_MS) 拿到真实值.
路径显式指向项目根 .env (parents[3] = charlotte_savanna), 不依赖进程 cwd;
测试通过 conftest 在 import 前直接设置环境变量, 覆盖 .env.
"""

import os
from pathlib import Path

import dotenv

dotenv.load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# 调 Django 内部端点 (图谱落库/失败标记) 的共享 token, 未配置时 Django 侧拒绝
INTERNAL_TOKEN = os.environ.get("CHARPLOT_INTERNAL_TOKEN", "")
# Django 业务侧基础 URL (FastAPI → Django 内部端点)
DJANGO_API_BASE = os.environ.get(
    "CHARPLOT_DJANGO_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")
# stub 管道每阶段演示时长 (ms), 测试置 0 避免慢测
STUB_DELAY_MS = int(os.environ.get("CHARPLOT_STUB_DELAY_MS", "800"))
