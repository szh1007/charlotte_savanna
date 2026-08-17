import os

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)

PGSQL_URL = (
    f"postgresql://{os.getenv('PGSQL_USERNAME', '')}:{os.getenv('PGSQL_PASSWORD', '')}\
    @{os.getenv('PGSQL_HOST', '')}:{os.getenv('PGSQL_PORT', '')}\
        /{os.getenv('PGSQL_NAME', '')}\
            ?sslmode=disable"
)


class OverAllState(MessagesState):
    output: str = Field(description="输出")


def llm_node(state: OverAllState) -> OverAllState:
    ai_messages = model.invoke(state["messages"])
    return {"messages": [ai_messages]}


def output_node(state: OverAllState) -> OverAllState:
    return {"output": state["messages"][-1].content}


builder = (
    StateGraph(state_schema=OverAllState)
    .add_node(llm_node)
    .add_node(output_node)
    .add_edge(START, "llm_node")
    .add_edge("llm_node", "output_node")
    .add_edge("output_node", END)
)

with PostgresSaver.from_conn_string(PGSQL_URL) as checkpointer:
    checkpointer.setup()

    graph = builder.compile(
        checkpointer=checkpointer,
        cache=InMemoryCache(),
    )

    config = {
        "configurable": {"thread_id": "langgraph_3_2_"},
    }

    rprint(
        graph.invoke(
            {"messages": [HumanMessage("你好, 我叫Charlotte")]},
            config=config,
            durability="async",  # 持久化模式 默认异步 async
        )
    )
    rprint(
        graph.invoke(
            {"messages": [HumanMessage("我是谁")]}, config=config, durability="async"
        )
    )

    # checkpointer.delete_thread("langgraph_3_2_")

    # 持久化模式 exit / async / sync
    # exit 退出: 运行退出时写入
    # async 异步:
    #   超步末尾触发异步写入任务, 超步任务完成后写入本次的中间结果
    # sync 同步: 进入下一个超步之前等待写入任务完成
