"""FastAPI 入口: 路由注册 + CORS + 健康检查 + 平台列表.

启动: uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .cleaner import cleaner
from .downloader import list_sites
from .routers import downloads, events, member, model, resolve, summarize
from .task_manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 交付文件目录 (下载落盘), 启动即创建
    config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # 模型目录 (ADR-0006): 语音转写模型本体 + 字幕缓存 (持久资产/缓存分离),
    # 启动即创建, 避免首次触发下载/缓存时才 mkdir (模型状态按文件判定)
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    config.SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
    cleaner.start()  # TTL 清理线程: 周期扫描过期交付, 删文件 + 标记 expired (T06)
    yield
    # 服务退出: 停止后台调度 + 清理线程 (daemon, 进程结束即终止, 显式停止防残留)
    manager.stop_scheduler()
    cleaner.stop()


app = FastAPI(
    title="BilibiliDownloader",
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
app.include_router(downloads.router)
app.include_router(events.router)
app.include_router(member.router)
app.include_router(summarize.router)
app.include_router(model.router)


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
