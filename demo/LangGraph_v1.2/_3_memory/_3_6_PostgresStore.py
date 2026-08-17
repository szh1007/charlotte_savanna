import os
from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.runtime import Runtime
from langgraph.store.postgres import PostgresStore
from loguru import logger
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
    username: str = Field(description="用户姓名")
    user_input: str = Field(description="用户输入")
    output: str = Field(description="模型输出")
    hobby: list[str] = Field(description="用户爱好")


def hobby_router(state: OverAllState) -> Literal["check_store_hobby", "llm"]:
    if state["hobby"]:
        logger.info("用户输入有爱好")
        return "llm"
    else:
        logger.info("用户输入无爱好")
        return "check_store_hobby"


def check_store_hobby_node(state: OverAllState, runtime: Runtime) -> OverAllState:
    logger.info("检查数据库是否有用户爱好")
    item = runtime.store.search(("Users",), filter={"name": state["username"]})[0]
    if not item:
        logger.warning(f"数据库用户 {state['username']} 不存在")
        return {"hobby": []}

    logger.info(f"数据库用户信息: {item}")
    return {"hobby": item.value["hobby"]}


def llm_node(state: OverAllState) -> OverAllState:
    messages = state["messages"] or []
    if not messages:  # 第1次问答, 初始化SystemMessage
        messages += [
            SystemMessage(content=("你是专业的智能问答助手, 请根据用户的偏好回答问题"))
        ]

    messages += [
        HumanMessage(
            content=(f"用户偏好: {state['hobby']}, 用户需求: {state['user_input']}")
        ),
    ]

    response = model.invoke(messages)
    return {"messages": [*messages, response], "output": response.content}


builder = (
    StateGraph(OverAllState)
    .add_node(check_store_hobby_node)
    .add_node(llm_node)
    .add_conditional_edges(
        START,
        hobby_router,
        path_map={
            "check_store_hobby": "check_store_hobby_node",
            "llm": "llm_node",
        },
    )
    .add_edge("check_store_hobby_node", "llm_node")
    .add_edge("llm_node", END)
)

with (
    PostgresStore.from_conn_string(PGSQL_URL) as store,
    PostgresSaver.from_conn_string(PGSQL_URL) as saver,
):
    saver.setup()
    store.setup()

    graph = builder.compile(checkpointer=saver, store=store)

    config = {
        "configurable": {"thread_id": "langgraph_3_6_"},
    }

    # store.put(
    #     namespace=("Users",),
    #     key="user_1",
    #     value={
    #         "name": "Charlotte",
    #         "age": "26",
    #         "hobby": ["Programming", "Experiment", "Working"],
    #     },
    # )
    # store.put(
    #     namespace=("Users",),
    #     key="user_2",
    #     value={
    #         "name": "Savanna",
    #         "age": "27",
    #         "hobby": ["Reading", "Writing", "Arting"],
    #     },
    # )

    # rprint(store.get(namespace=("Users",), key="user_1"))
    # rprint(store.get(namespace=("Users",), key="user_2"))
    # rprint(f"store search:\n{store.search(('Users',))}")
    # rprint(f"store search:\n{store.search(('Users',), filter={'name': 'Charlotte'})}")

    rprint(
        graph.invoke(
            {
                "username": "Charlotte",
                "user_input": ("你好, 最近有点无聊, 请推荐一些有意思的娱乐活动"),
                "hobby": ["剧本杀"],
            },
            config=config,
        )["output"]
    )
    rprint("-" * 50)

    rprint(
        graph.invoke(
            {
                "username": "Charlotte",
                "user_input": ("你好, 最近有点无聊, 请推荐一些有意思的娱乐活动"),
                "hobby": [],
            },
            config=config,
        )["output"]
    )
    rprint("-" * 50)

    rprint(
        graph.invoke(
            {
                "username": "Savanna",
                "user_input": ("你好, 最近有点无聊, 请推荐一些有意思的娱乐活动"),
                "hobby": ["剧本杀"],
            },
            config=config,
        )["output"]
    )
    rprint("-" * 50)

    rprint(
        graph.invoke(
            {
                "username": "Savanna",
                "user_input": ("你好, 最近有点无聊, 请推荐一些有意思的娱乐活动"),
                "hobby": [],
            },
            config=config,
        )["output"]
    )
