import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


class OverAllState(MessagesState):
    output: str = Field(description="输出")


def llm_node(state: OverAllState) -> OverAllState:
    ai_messages = model.invoke(state["messages"])
    return {"messages": [ai_messages]}


def output_node(state: OverAllState) -> OverAllState:
    return {"output": state["messages"][-1].content}


graph = (
    StateGraph(state_schema=OverAllState)
    .add_node(llm_node)
    .add_node(output_node)
    .add_edge(START, "llm_node")
    .add_edge("llm_node", "output_node")
    .add_edge("output_node", END)
).compile(
    checkpointer=InMemorySaver(),
    cache=InMemoryCache(),
)

config = {
    "configurable": {"thread_id": "langgraph_3_1_"},
}

rprint(graph.invoke({"messages": [HumanMessage("你好, 我叫Charlotte")]}, config=config))
rprint(graph.invoke({"messages": [HumanMessage("我是谁")]}, config=config))
