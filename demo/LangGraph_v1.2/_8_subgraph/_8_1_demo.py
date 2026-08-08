from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

""" 子图 """


class SubOverAllState(MessagesState):
    raw_text: str = Field(description="原始文本")
    stripped_text: str = Field(description="去除首尾空格")
    punctuated_text: str = Field(description="句尾添加句号")


def subgraph_strip_node(state: SubOverAllState) -> SubOverAllState:
    return {"stripped_text": state["raw_text"].strip()}


def subgraph_punctuate_node(state: SubOverAllState) -> SubOverAllState:
    return {"punctuated_text": state["stripped_text"] + "."}


subgraph = (
    StateGraph(SubOverAllState)
    .add_node(subgraph_strip_node)
    .add_node(subgraph_punctuate_node)
    .add_edge(START, "subgraph_strip_node")
    .add_edge("subgraph_strip_node", "subgraph_punctuate_node")
    .add_edge("subgraph_punctuate_node", END)
).compile()


""" 主图 """
config = {
    "configurable": {"thread_id": "langgraph_8_1_"},
}


"""
方法1: 在主图的节点中直接调用子图
注意: 这种方式主图是看不到子图的节点路线的
"""


class OverAllState(MessagesState):
    input_text: str = Field(description="输入文本")
    cleaned_text: str = Field(description="清理后的文本")


def call_subgraph(state: OverAllState) -> OverAllState:
    result = subgraph.invoke({"raw_text": state["input_text"]})
    return {"cleaned_text": result["punctuated_text"]}


graph1 = (
    StateGraph(OverAllState)
    .add_node(call_subgraph)
    .add_edge(START, "call_subgraph")
    .add_edge("call_subgraph", END)
).compile(checkpointer=InMemorySaver())

# rprint(graph1.invoke({"input_text": "  你好, 我是 Charlotte   "}, config=config))

# # 使用 checkpoint_ns 区别主图和子图
# rprint(list(graph1.get_state_history(config=config)))
# rprint(graph1.get_state(config=config, subgraphs=True))

for chunk in graph1.stream(
    {"input_text": "  你好, 我是 Charlotte   "},
    subgraphs=True,
    stream_mode=["updates"],
    config=config,
):
    rprint(chunk)

"""
方法2: 把子图作为节点添加到主图中
注意: 这种方法可以看到子图的节点路线
"""
# graph2 = (
#     StateGraph(SubOverAllState)  # 注意点1: 子图和主图的全局状态要一致
#     .add_node("subgraph", subgraph)  # 注意点2: 子图节点要命名
#     .add_edge(START, "subgraph")
#     .add_edge("subgraph", END)
# ).compile(checkpointer=InMemorySaver())
# rprint(graph2.invoke({"raw_text": "  你好, 我是 Charlotte   "}, config=config))
# rprint(list(graph2.get_state_history(config=config)))
# rprint(graph2.get_state(config=config, subgraphs=True))
