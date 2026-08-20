"""模型路由 (ADR-0006): 语音转写模型状态查询与手动下载.

契约:
- GET  /api/model/status → {status, progress, has_official_subtitle}
  status: missing | downloading | ready (ready = config.yaml + model.pt 存在,
  以文件为准同步); has_official_subtitle = 是否配置了 B 站 cookie (官方字幕
  可用性, 前端据此提示切换)
- POST /api/model/download → {status, progress}; 幂等: ready 不动作,
  downloading 返回当前进度, missing 启动后台下载线程 (daemon, 独立于任务
  生命周期, 任务取消不中断)

下载进度经 SSE model-update 事件广播 (publish_all, 不受 task_id 过滤),
与任务事件同流. 模型为全局持久资产 (~1GB), 下载失败自动回 missing 可重试.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..model_downloader import model_downloader

router = APIRouter(tags=["model"])


@router.get("/api/model/status")
def model_status() -> dict[str, Any]:
    """模型状态: 状态机 (missing/downloading/ready) + 进度 + 官方字幕可用性.

    has_official_subtitle 由配置的 B 站 cookie 决定: 未配置时官方字幕
    快路径不可用, 前端提示「官方字幕不可用, 将自动切换模型生成」.
    """
    return model_downloader.status()


@router.post("/api/model/download")
def download_model() -> dict[str, Any]:
    """下载语音转写模型 (幂等): 已就绪 / 下载中均不重复触发.

    缺失时启动后台下载线程立即返回 (下载约 1GB, 进度经 SSE model-update
    广播), 失败自动回 missing, 前端可再次触发.
    """
    return model_downloader.download()
