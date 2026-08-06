import time
from operator import add
from typing import Annotated

from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import CachePolicy
from loguru import logger
from pydantic import Field


class OverAllState(MessagesState):
    user: str = Field(description="用户ID")
    invoke_counts: Annotated[int, Field(description="调用次数"), add]


def test_node(state: OverAllState) -> OverAllState:
    logger.info(f"{state['user']} 执行测试节点...")

    # 模拟耗时操作
    time.sleep(3)

    logger.info(f"{state['user']} 测试节点执行完成")
    return {"invoke_counts": 1}


builder = StateGraph(state_schema=OverAllState)
builder.add_node(
    test_node,
    cache_policy=CachePolicy(
        ttl=10,  # 缓存过期时间, 单位-秒
        key_func=None,  # 缓存键生成函数
        # (给复杂的、不可序列化的数据使用), 默认使用输入参数
    ),
)
builder.add_edge(START, "test_node")
builder.add_edge("test_node", END)

graph = builder.compile(cache=InMemoryCache())

# 相同输入 + 确定的输出 -> 使用缓存
logger.info(f"第1次调用结果: {graph.invoke({'user': 'charlotte', 'invoke_counts': 0})}")
logger.info(f"第2次调用结果: {graph.invoke({'user': 'charlotte', 'invoke_counts': 0})}")

# 不同输入 -> 输出必定不同 -> 不使用缓存
logger.info(f"第3次调用结果: {graph.invoke({'user': 'savanna', 'invoke_counts': 0})}")
