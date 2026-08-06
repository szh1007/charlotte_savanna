import os
from typing import Literal, TypedDict

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from loguru import logger
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)

PG_DB_URL = (
    f"postgresql://{os.getenv('PG_DB_USERNAME', '')}:{os.getenv('PG_DB_PASSWORD', '')}\
    @{os.getenv('PG_DB_HOST', '')}:{os.getenv('PG_DB_PORT', '')}\
        /{os.getenv('PG_DB_NAME', '')}\
            ?sslmode=disable"
)


class OverAllState(MessagesState):
    username: str = Field(description="用户名")
    user_input: str = Field(description="用户输入")
    output: str = Field(description="输出")


class StructuredOutputState(TypedDict):
    topic: str = Field(description="主题")
    mode: Literal["poem", "joke"] = Field(description="模式")


model_with_structure = model.with_structured_output(StructuredOutputState)


def router_node(state: OverAllState) -> StructuredOutputState:
    logger.info("开始路由...")
    structured_output = model_with_structure.invoke([HumanMessage(state["user_input"])])
    logger.info(f"路由结果: {structured_output}")
    return structured_output


def router(state: StructuredOutputState) -> Literal["poem", "joke", "default"]:
    if state["mode"] == "poem":
        logger.info("路由 - 诗")
        return "poem"
    elif state["mode"] == "joke":
        logger.info("路由 - 笑话")
        return "joke"
    else:
        logger.info("路由 - 默认")
        return "default"


def poem_node(state: StructuredOutputState) -> OverAllState:
    logger.info(f"开始写诗, 主题: {state['topic']}")
    poem = model.invoke(
        [
            HumanMessage(
                f"请写一首关于{state['topic']}的七言诗, 不展示分析过程直接输出诗"
            )
        ]
    ).content
    return {"output": f"主题: {state['topic']}\n\n诗: {poem}"}


def joke_node(state: StructuredOutputState) -> OverAllState:
    logger.info(f"开始写笑话, 主题: {state['topic']}")
    joke = model.invoke(
        [
            HumanMessage(
                f"请写一个关于{state['topic']}的笑话, 同时还是解释笑点, 并解释笑点背景"
            )
        ]
    ).content
    return {"output": f"主题: {state['topic']}\n\n笑话: {joke}"}


def default_node(state: StructuredOutputState) -> OverAllState:
    logger.info("默认路由")
    return {"output": "默认路由"}


builder = (
    StateGraph(state_schema=OverAllState)
    .add_node(router_node)
    .add_node(poem_node)
    .add_node(joke_node)
    .add_node(default_node)
    .add_edge(START, "router_node")
    .add_conditional_edges(
        "router_node",
        router,
        path_map={"poem": "poem_node", "joke": "joke_node", "default": "default_node"},
    )
    .add_edge("poem_node", END)
    .add_edge("joke_node", END)
    .add_edge("default_node", END)
)

with PostgresSaver.from_conn_string(PG_DB_URL) as checkpointer:
    checkpointer.setup()

    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {"thread_id": "langgraph_3_5_"},
    }

    # 第1次运行: 生成检查点
    # rprint(
    #     graph.invoke(
    #         {"username": "charlotte",
    #          "user_input": "写一首关于猫的诗"}, config=config
    #     )
    # )
    # rprint(list(graph.get_state_history(config)))

    # 第2次运行: 修改指定检查点的输入
    before_router_checkpoint = next(
        h for h in graph.get_state_history(config) if h.next == ("router_node",)
    )
    fork_change_input_config = graph.update_state(
        config=before_router_checkpoint.config,
        values={"user_input": "写一个关于猫的笑话"},
        as_node=START,
    )
    rprint(before_router_checkpoint)
    rprint(fork_change_input_config)

    # 第3次运行: 从上述指定检查点fork
    rprint(graph.invoke(None, config=fork_change_input_config))

    # 解释 as_node: 从哪个节点开始 fork
    # 1.如果指定为节点自身, 则表示修改自身节点的输入,
    #   【重新生成】后续的输出
    # 2.如果指定为其他节点, 实际表现为可以【跳过】某些节点
    # 仅测试时使用, 此处不做演示了
