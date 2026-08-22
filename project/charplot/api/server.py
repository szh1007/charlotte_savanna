"""CharPlot FastAPI 服务 - AI 能力端.

职责 (ADR-0001): 知识管道 / RAG 全链路 / 任务系统, 骨架阶段仅提供健康检查.
Django 侧 = 状态与数据 (app/charplot), FastAPI 侧 = AI 能力, 二者通过
HTTP + 共享 MySQL/Redis 通信.
"""

import os
from datetime import datetime

import dotenv
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

dotenv.load_dotenv()

app = FastAPI(title="CharPlot AI Service", version="0.1.0")

# 前端开发服务器跨域访问 (Vite dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ai/health")
async def health():
    """健康检查 - 探活共享 Redis.

    三端联通的基础链路 (Issue 01): 前端可轮询此端点确认 AI 服务就绪.
    """
    redis_status = "ok"
    try:
        client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
        )
        await client.ping()
        await client.aclose()
    except Exception:
        redis_status = "error"

    payload = {
        "status": "ok" if redis_status == "ok" else "degraded",
        "service": "charplot-fastapi",
        "redis": redis_status,
        "time": datetime.now().isoformat(),
    }
    return payload


if __name__ == "__main__":
    # 独立启动: python -m project.charplot.api.server
    uvicorn.run(app, host="127.0.0.1", port=8001)
