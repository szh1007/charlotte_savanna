"""启动预热 (L5-D3): 服务起来之后, 后台把两个本地模型加载进内存.

**为什么非做不可**: 两个模型 (bge-m3 + bge-reranker-v2-m3) 加载起来要十几秒
(2026-10-04 实测: 串行预热合计 7~8 秒, 之后进程 RSS 约 3.4GB), 而这一次开销
**每个进程只付一次**. 不预热的话, 演示时"第一问"会卡在那里 —— 共享桌面面试场景下
这是致命的, 而它完全可以提前到"服务刚起来、没人提问"的那几十秒里做. 预热就是把
这段等待从**第一个用户**身上挪到**启动期**的账上.

**它是后台任务, 不阻塞启动**: 进程照常几百毫秒内起来、健康检查立刻能过; 两个模型
在线程里加载 (`to_thread`), 只是顺序是**串行**的 —— 并行加载会撞 transformers 与
accelerate 的全局态竞态 (两个线程同时在 `init_empty_weights` 那一层改
`nn.Module.register_parameter`, 先回来的那个可能拿到一个**没装上权重**的 meta 模型,
症状是第一次真正推理时抛 `Cannot copy out of meta tensor`; 2026-10-04 实测复现,
并行时好时坏, 串行稳定). 代价是启动期多等两三秒, 而那几秒本来就是"还没人提问"的.

**失败不致命, 但要吵**: 缺 embedding 模型时 RAG 整条链路不可用, 而助手的别的
能力 (查单 / 查余额 / 下单) 一个都不该受影响 —— 所以这里 log.error 并返回报告,
**不抛异常**. 缺 reranker 只是"降级不精排" (设计内的分支, 见 `rerank.py`), 记
warning. 两种"没就绪"都写进返回值, 启动那行日志因此说得清这次预热到底成了没有.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import NamedTuple

from CharApp.minimall.config import KnowledgeConfig
from CharApp.minimall.knowledge.embeddings import get_embedder
from CharApp.minimall.knowledge.rerank import get_reranker, rerank_status

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PrewarmReport:
    """预热结果 (启动日志与用例看的就是它).

    attributes:
        embedder_ready: embedding 模型是否已加载. False = RAG 检索不可用
            (`embedder_error` 是原因).
        embedder_error: 加载失败的原文 (缺失模型时那条带下载命令的运行期错误).
        reranker_ready: 精排模型是否已加载. False 不一定是错 —— 没配或本地缺失
            都会走 Noop 降级, 那是设计内的分支.
        reranker_reason: 降级原因 (就绪时是空串).
    """

    embedder_ready: bool
    embedder_error: str | None
    reranker_ready: bool
    reranker_reason: str


class _LoadResult(NamedTuple):
    """一个模型的加载结果; ready=False 时 detail 是原因 (报错原文 / 降级理由)."""

    ready: bool
    detail: str


async def prewarm(config: KnowledgeConfig) -> PrewarmReport:
    """后台加载两个模型 (谁也不会因为这里失败而拦住服务启动).

    Returns:
        PrewarmReport: 两个模型各自就绪 / 未就绪 + 原因.
    """
    # 串行, 不并行: 理由见模块 docstring 里那条实测 (并行会撞 transformers 的
    # 全局态竞态, 拿到没装权重的 meta 模型). 各自仍丢线程池 —— 事件循环不阻塞.
    embedder = await asyncio.to_thread(_load_embedder, config)
    reranker = await asyncio.to_thread(_load_reranker, config)
    report = PrewarmReport(
        embedder_ready=embedder.ready,
        embedder_error=None if embedder.ready else embedder.detail,
        reranker_ready=reranker.ready,
        reranker_reason="" if reranker.ready else reranker.detail,
    )
    logger.info(
        "知识库模型预热完成: embedding=%s, rerank=%s",
        "就绪" if report.embedder_ready else f"未就绪 ({report.embedder_error})",
        "就绪" if report.reranker_ready else f"降级 ({report.reranker_reason})",
    )
    return report


def _load_embedder(config: KnowledgeConfig) -> _LoadResult:
    """加载 embedding 模型 (失败只记日志, 见模块 docstring)."""
    try:
        embedder = get_embedder(config)
        preload = getattr(embedder, "preload", None)
        if callable(preload):
            preload()
    except Exception as exc:
        logger.error(
            "embedding 模型预热失败, RAG 检索在修好之前不可用 (其余功能不受影响): %s",
            exc,
        )
        return _LoadResult(False, f"{type(exc).__name__}: {exc}")
    return _LoadResult(True, "")


def _load_reranker(config: KnowledgeConfig) -> _LoadResult:
    """加载精排模型; Noop 降级不算异常 (原因原样带出来).

    降级与否走 `rerank_status` 这**同一个判断** —— 预热结果与检索时实际用的那个
    决定因此不可能对不上 (两处各判一次就会漂).
    """
    status = rerank_status(config)
    if status["degraded"]:
        return _LoadResult(False, status["reason"])
    try:
        preload = getattr(get_reranker(config), "preload", None)
        if callable(preload):
            preload()
    except Exception as exc:
        logger.warning("reranker 预热失败, 检索保持不精排: %s", exc)
        return _LoadResult(False, f"{type(exc).__name__}: {exc}")
    return _LoadResult(True, "")


__all__ = ["PrewarmReport", "prewarm"]
