from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
from loguru import logger
from requests import HTTPError
from rich import print as rprint


class EmptyState(MessagesState):
    pass


def test_node(state: EmptyState) -> EmptyState:
    logger.info("执行测试节点...")
    raise HTTPError("模拟抛出异常")


def error_handler(state: EmptyState, error: NodeError) -> Command:
    rprint(f"节点【{error.node}】失败, 错误信息: {error.error}")
    return Command(
        update={"status": f"compensation triggered for: {error.error}"},
        goto="finalize",
    )


def finalize(state: EmptyState) -> EmptyState:
    logger.info("执行最终节点")
    return state


builder = StateGraph(state_schema=EmptyState)
builder.add_node(
    test_node,
    retry_policy=RetryPolicy(
        max_attempts=5,  # 最大重试次数
        initial_interval=0.5,  # 初始重试间隔
        max_interval=4,  # 最大重试间隔
        backoff_factor=2,  # 重试间隔倍数
        jitter=True,  # 是否添加随机偏移
        retry_on=[HTTPError],  # 需要重试的异常类型, 一般不需要手动修改, 除非有自定义异常
    ),
    timeout=TimeoutPolicy(  # 超时控制
        run_timeout=10,  # 超时时间
    ),
    error_handler=error_handler,  # 异常处理
)
builder.add_node(finalize)

builder.add_edge(START, "test_node")
builder.add_edge("test_node", "finalize")
builder.add_edge("finalize", END)

graph = builder.compile()

graph.invoke({})
