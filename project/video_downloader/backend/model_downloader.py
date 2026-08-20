"""语音转写模型下载 (ADR-0006): 全局唯一状态机 + 幂等触发 + SSE 进度.

状态机: missing → downloading → ready; 下载失败回 missing 可重试.
ready 判定 = 模型配置与权重文件均存在 (MODELS_DIR/SenseVoiceSmall/ 下
config.yaml + model.pt), 以文件为准, 不依赖内存状态残留.

下载引擎为独立调用点 (_snapshot_download, modelscope snapshot_download):
测试替换该函数驱动状态流转 / 进度回调 / 失败, 不触网. 下载线程为全局
单例 daemon, 与总结任务解耦: 任务取消 (清除记录) 不中断模型下载
(模型是全局资产, 已下载部分继续, 重触发由下载覆盖续传).

进度经 SSE 新增 model-update 事件广播 (events.publish_all): 与任务事件
同一条流, 不受 task_id 过滤影响 (订阅者即使只关注指定任务也收到模型进度).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config
from .events import bus, model_update_event

logger = logging.getLogger(__name__)

# 模型状态机三态 (API 契约 / SSE 负载)
STATUS_MISSING = "missing"
STATUS_DOWNLOADING = "downloading"
STATUS_READY = "ready"

# ready 判定所需文件 (config.yaml + model.pt, ADR-0006)
_READY_FILES = ("config.yaml", "model.pt")


def model_dir() -> Path:
    """模型本地目录 (MODELS_DIR/SenseVoiceSmall/, env 可覆盖)."""
    return config.MODELS_DIR / config.MODEL_DIR_NAME


def is_ready() -> bool:
    """模型是否就绪: 配置与权重文件均存在 (文件为唯一事实源)."""
    d = model_dir()
    return all((d / f).is_file() for f in _READY_FILES)


def status() -> dict[str, Any]:
    """模块级快捷: 全局单例状态快照 (task_manager / 路由共用)."""
    return model_downloader.status()


def download() -> dict[str, Any]:
    """模块级快捷: 幂等触发下载 (见 ModelDownloader.download)."""
    return model_downloader.download()


def _snapshot_download(
    model_id: str,
    local_dir: Path,
    callback: Callable[[str, int, int], None] | None = None,
) -> None:
    """modelscope 下载模型到指定目录, 回调进度 (文件名, 已下载, 总数).

    独立的引擎调用点: 测试通过替换本函数 mock 下载过程 (写文件 + 报进度),
    不依赖真实 modelscope 与网络. ADR-0006 确认本机 modelscope 1.39.1
    的 snapshot_download 支持 local_dir 直落与进度回调.
    """
    from modelscope import snapshot_download

    snapshot_download(
        model_id,
        local_dir=str(local_dir),
        allow_file_pattern=None,
        ignore_file_pattern=None,
        callback=callback,
    )


class ModelDownloader:
    """全局模型下载状态机 (线程安全): 状态 + 进度 + 幂等触发."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = STATUS_MISSING
        self._progress = 0.0
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        """当前状态快照: {status, progress, has_official_subtitle}.

        has_official_subtitle = 服务端是否配置 BILI_COOKIE (官方字幕能力
        是否可用); ready 判定以文件为准, 文件变化 (删除/补齐) 即时反映.
        """
        with self._lock:
            if self._status != STATUS_DOWNLOADING and is_ready():
                self._status = STATUS_READY
                self._progress = 100.0
            return {
                "status": self._status,
                "progress": self._progress,
                "has_official_subtitle": bool(config.BILI_COOKIE),
            }

    def download(self) -> dict[str, Any]:
        """幂等触发下载: 已就绪不动作 / 下载中返回当前进度 / 缺失启动.

        返回与 status() 同构的状态快照; 下载线程单例 (daemon), 重复触发
        不会重复下载. 下载失败由 worker 回 missing, 下次触发可重试.
        广播与 status() 在锁外执行 (普通 Lock 不可重入, 且避免持锁广播).
        """
        with self._lock:
            if is_ready():
                self._status = STATUS_READY
                self._progress = 100.0
            elif self._thread is not None and self._thread.is_alive():
                pass  # 下载中: 返回当前进度, 不重复启动
            else:
                self._status = STATUS_DOWNLOADING
                self._progress = 0.0
                self._thread = threading.Thread(
                    target=self._download_worker,
                    name="model-downloader",
                    daemon=True,
                )
                self._thread.start()
        if self._status == STATUS_DOWNLOADING:
            bus.publish_all(model_update_event(STATUS_DOWNLOADING, self._progress))
        return self.status()

    def _download_worker(self) -> None:
        """执行下载: 进度回调更新状态 + 广播; 完成后校验文件, 失败回 missing.

        文件校验失败 (下载不完整 / 中途异常) 与下载引擎异常同路径: 状态回
        missing, 进度清零, 可重新触发下载 (已下载文件由引擎续传覆盖).
        """
        try:
            _snapshot_download(config.ASR_MODEL, model_dir(), self._on_progress)
            if not is_ready():
                raise RuntimeError("模型文件不完整 (缺少 config.yaml / model.pt)")
            with self._lock:
                self._status = STATUS_READY
                self._progress = 100.0
            bus.publish_all(model_update_event(STATUS_READY, 100.0))
            logger.info("语音转写模型下载完成: %s", model_dir())
        except Exception as e:
            logger.warning("模型下载失败: %s", e)
            with self._lock:
                self._status = STATUS_MISSING
                self._progress = 0.0
            bus.publish_all(model_update_event(STATUS_MISSING, 0.0))

    def _on_progress(self, filename: str, done: int, total: int) -> None:
        """引擎进度回调 → 状态 + 进度 + SSE 广播 (下载中, 单调递增)."""
        pct = min((done / total) * 100, 99.0) if total else 0.0
        with self._lock:
            self._progress = pct
        bus.publish_all(model_update_event(STATUS_DOWNLOADING, pct))


# 模块级单例: 路由 / task_manager / 前端共享同一状态机
model_downloader = ModelDownloader()
