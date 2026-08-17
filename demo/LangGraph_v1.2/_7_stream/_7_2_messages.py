import os

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)


def llm_node(state: MessagesState) -> MessagesState:
    return {"messages": [model.invoke(state["messages"])]}


graph = (
    StateGraph(MessagesState)
    .add_node(llm_node)
    .add_edge(START, "llm_node")
    .add_edge("llm_node", END)
    .compile()
)

for chunk in graph.stream(
    {"messages": [HumanMessage(content="你好")]},
    stream_mode=["values", "messages"],
    version="v2",
):
    # if chunk[0] == "messages":
    #     rprint(chunk[1][0].content, end="")

    # if chunk[0] == "values" and len(chunk[1]["messages"]) > 1:
    #     rprint("\n", "-" * 50)
    #     rprint(f"values: {chunk[1]['messages'][-1].content}")

    # v2
    if chunk["type"] == "messages":
        rprint(chunk["data"][0].content, end="")
