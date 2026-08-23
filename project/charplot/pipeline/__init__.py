"""CharPlot 知识管道 (DESIGN.md §3).

统一管道 (ADR-0002): 归一化解析 → 主内容分析 → 联网搜索增强 → 知识解构.
本票 (Issue 03) 为 stub 实现 (pipeline/stub.py 确定性模板图谱);
Issue 07 以 LangGraph 真实管道替换 run_pipeline 函数体, 签名与契约不变
(无缝替换点, 见 CONTRACT.md).
"""

import asyncio
from dataclasses import dataclass

from ..api.config import STUB_DELAY_MS
from . import stub

# 阶段序与进度 (DESIGN §4.2 SSE 契约, progress 单调递增)
STAGES = ["parsing", "analyzing", "searching", "deconstructing"]
STAGE_PROGRESS = {"parsing": 15, "analyzing": 35, "searching": 60, "deconstructing": 90}
STAGE_MESSAGES = {
    "parsing": "正在解析输入内容…",
    "analyzing": "正在分析主题结构…",
    "searching": "正在搜索相关知识…",
    "deconstructing": "正在解构知识图谱…",
}


@dataclass
class PipelineInput:
    """管道入参. content 在 file 输入时为空 (文件内容解析是 Issue 07)."""

    journey_id: int
    input_type: str  # text | file | link
    content: str = ""


async def run_pipeline(inp: PipelineInput, emit) -> dict:
    """执行知识管道, 返回契约图谱 dict (CONTRACT.md §1).

    emit(stage, progress, message) async 回调上报进度 (任务系统写入 Redis + SSE).
    每阶段 sleep 模拟耗时, 真实管道 (Issue 07) 替换函数体即可.
    """
    for stage in STAGES:
        await emit(stage, STAGE_PROGRESS[stage], STAGE_MESSAGES[stage])
        await asyncio.sleep(STUB_DELAY_MS / 1000)
    return stub.generate_graph(inp)
