import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, interrupt
from pydantic import Field
from rich import print as rprint

"""
子图 checkpointer=True 时

Tips 1 多次请求主图
每个【主图请求内】的子图记忆是【连续】的
【主图请求之间】的子图记忆是【相互独立】的

Tips 2 主图中调用不同的子图
推荐使用不同的节点】调用【不同的子图】, 防止因为调用子图的顺序问题, 导致记忆错乱
"""

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


""" 子图 """


class OverAllState(MessagesState):
    raw_text: str = Field(description="原始文本")
    stripped_text: str = Field(description="去除首尾空格")
    cleaned_text: str = Field(description="清理后的文本")


def subgraph_strip_node(state: OverAllState) -> OverAllState:
    return {
        "messages": (
            [HumanMessage(state.get("raw_text", "").strip())]
            if state["raw_text"]
            else []
        ),
        "stripped_text": state["raw_text"].strip(),
    }


def subgraph_punctuate_node(state: OverAllState) -> OverAllState:
    cleaned_text = state["stripped_text"]
    punctuation1 = interrupt("句首添加的标点符号: ")
    punctuation2 = interrupt("句尾添加的标点符号: ")
    return {
        "messages": [model.invoke(state["messages"])],
        "cleaned_text": punctuation1 + cleaned_text + punctuation2,
    }


subgraph = (
    StateGraph(OverAllState)
    .add_node(subgraph_strip_node)
    .add_node(subgraph_punctuate_node)
    .add_edge(START, "subgraph_strip_node")
    .add_edge("subgraph_strip_node", "subgraph_punctuate_node")
    .add_edge("subgraph_punctuate_node", END)
).compile(
    # checkpointer=None,  # 有检查点, 可以中断恢复, 没有多轮记忆
    checkpointer=True,  # 有检查点, 可以中断恢复, 有多轮记忆
    # checkpointer=False,  # 没有任何检查点状态和记忆, 中断不可用
)


""" 主图 """
config = {
    "configurable": {"thread_id": "langgraph_8_2_"},
}

graph2 = (
    StateGraph(OverAllState)
    .add_node("subgraph", subgraph)
    .add_edge(START, "subgraph")
    .add_edge("subgraph", END)
).compile(checkpointer=InMemorySaver())

# 第1次对话
rprint(graph2.invoke({"raw_text": "  你好, 我是 Charlotte   "}, config=config))
rprint(graph2.invoke(Command(resume="!"), config=config))
rprint(graph2.invoke(Command(resume="!"), config=config))
# for chunk in graph2.stream(
#     Command(resume="!"),
#     subgraphs=True,
#     stream_mode=["messages"],
#     config=config,
# ):
#     rprint(chunk)

# # 第2次对话
# rprint(
#     graph2.invoke(
#         {"messages": [HumanMessage("我是谁")], "raw_text": " 我是谁 "}, config=config
#     )
# )
# rprint(graph2.invoke(Command(resume="!"), config=config))
# rprint(graph2.invoke(Command(resume="!"), config=config))
