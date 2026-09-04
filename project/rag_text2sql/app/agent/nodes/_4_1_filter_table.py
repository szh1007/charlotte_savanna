import asyncio

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    await asyncio.sleep(1)
    writer = runtime.stream_writer
    writer({"stage": "过滤表信息"})
