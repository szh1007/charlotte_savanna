import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


class OverAllState(MessagesState):
    username: str = Field(description="用户名")
    input: str = Field(description="输入内容")
    output: str = Field(description="输出内容")


def node_start(state: OverAllState) -> OverAllState:
    return {"messages": [HumanMessage(content=state["input"])]}


def node_llm(state: OverAllState) -> OverAllState:
    response = model.invoke(state["messages"])
    return {"messages": [response], "output": response.content}


def node_end(state: OverAllState) -> OverAllState:
    return {"output": state["output"]}


builder = StateGraph(state_schema=OverAllState)

# # 方法1
# builder.add_node(node_start)
# builder.add_node(node_llm)
# builder.add_node(node_end)

# builder.add_edge(START, "node_start")
# builder.add_edge("node_start", "node_llm")
# builder.add_edge("node_llm", "node_end")
# builder.add_edge("node_end", END)

# 方法2 - add_sequence
builder.add_edge(START, "node_start")  # Start To 不可省略
builder.add_sequence([node_start, node_llm, node_end])
builder.add_edge("node_end", END)  # To End 可省略, 但推荐写上

graph = builder.compile()

result = graph.invoke({"username": "charlotte", "input": "你好"})
rprint(result)
