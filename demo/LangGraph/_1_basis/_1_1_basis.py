from operator import add
from time import sleep
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite
from pydantic import Field
from rich import print as rprint

"""state"""
# Pydantic: 严格的格式校验
# TypeDict: 轻量级数据定义


class OverAllState(TypedDict):
    """全局状态"""

    logs: Annotated[
        list[str], Field(description="日志列表"), add
    ]  # 归约函数-reducer add
    cur_id: str = Field(description="当前节点ID")


class InputState(TypedDict):
    """输入状态 (全局状态的子集)"""

    cur_id: str


class OutputState(TypedDict):
    """输出状态 (全局状态的子集)"""

    logs: list[str]
    cur_id: str


# Tip 私有状态: 仅在节点内部使用, 不受全局状态限制 (避免与全局状态的字段重名)


"""node"""
# Pydantic - 返回 state 实例: 严格的格式验证 (也可以返回 dict)
# TypedDict - 返回 dict: 可选择性的传递字段

# Overwrite: 跳过归约函数 reducer, 直接覆盖

# 如果同时接受多个节点的输出, 更新的字段【必须设置归约函数 reducer】, 否则【会报错】


def node_start(state: InputState) -> OverAllState:
    for k, v in state.items():
        rprint(f"S-{k}: {v}")
    rprint("-" * 20)

    return OverAllState(logs=["start finished"], cur_id=state["cur_id"] + "-doing")


def node_1(state: OverAllState) -> OverAllState:
    sleep(1)

    for k, v in state.items():
        rprint(f"1-{k}: {v}")
    rprint("-" * 20)

    return {"logs": ["1 finished"]}


def node_2(state: OverAllState) -> OverAllState:
    sleep(2)

    for k, v in state.items():
        rprint(f"2-{k}: {v}")
    rprint("-" * 20)

    return {"logs": ["2 finished"]}


def node_end(state: OverAllState) -> OutputState:
    for k, v in state.items():
        rprint(f"E-{k}: {v}")
    rprint("-" * 20)

    return {
        "logs": Overwrite(["end finished"]),
        "cur_id": "END",
    }


"""graph"""
builder = StateGraph(
    state_schema=OverAllState,  # 全局状态
    input_schema=InputState,  # 输入状态
    output_schema=OutputState,  # 输出状态
)

# 添加节点时, 节点的入参状态中的字段会被全局识别, 从而能正常使用
builder.add_node(node_start)
builder.add_node(node_1)
builder.add_node(node_2)
builder.add_node(node_end)

builder.add_edge(START, "node_start")
builder.add_edge("node_start", "node_1")
builder.add_edge("node_start", "node_2")
builder.add_edge("node_1", "node_end")
builder.add_edge("node_2", "node_end")
builder.add_edge("node_end", END)

graph = builder.compile()

rprint(graph.get_graph().draw_mermaid())  # 图结构可视化


"""RUN"""
# 输入 (同上): state 实例 / dict
result = graph.invoke({"cur_id": "START"})
# result = graph.invoke(OverAllState(logs=[], cur_id="START"))

rprint(result)
