"""FastAPI 入口: 路由注册 + CORS + 健康检查 + 平台列表.

启动: uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .downloader import list_sites
from .routers import resolve


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 交付文件目录 (T02+ 落盘), 启动即创建
    config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="万能视频下载站",
    version="0.1.0",
    lifespan=lifespan,
)

# 前端为独立工程 (Vite dev server 跨域), 开发期允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(resolve.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "video-downloader"}


@app.get("/api/sites")
def sites() -> dict:
    site_list, total = list_sites()
    return {"sites": site_list, "total": total}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
