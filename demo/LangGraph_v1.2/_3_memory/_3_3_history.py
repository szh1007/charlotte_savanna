import os

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
    topic: str = Field(description="主题")
    poem: str = Field(description="诗")
    joke: str = Field(description="笑话")
    final_output: str = Field(description="最终输出")


class InputState(MessagesState):
    topic: str = Field(description="主题")


class OutputState(MessagesState):
    final_output: str = Field(description="最终输出")


def poem_node(state: InputState) -> OverAllState:
    logger.info(f"开始写诗, 主题: {state['topic']}")
    topic = state["topic"]
    poem = model.invoke([HumanMessage(f"请写一首关于{topic}的七言绝句")]).content
    return {"poem": poem}


def joke_node(state: InputState) -> OverAllState:
    logger.info(f"开始写笑话, 主题: {state['topic']}")
    topic = state["topic"]
    joke = model.invoke([HumanMessage(f"请写一个关于{topic}的笑话")]).content
    return {"joke": joke}


def output_node(state: OverAllState) -> OutputState:
    logger.info(f"开始写最终输出, 主题: {state['topic']}")
    final_output = f"主题: {state['topic']}\n诗: {state['poem']}\n笑话: {state['joke']}"
    return {"final_output": final_output}


builder = (
    StateGraph(
        state_schema=OverAllState,
        input_schema=InputState,
        output_schema=OutputState,
    )
    .add_node(poem_node)
    .add_node(joke_node)
    .add_node(output_node)
    .add_edge(START, "poem_node")
    .add_edge(START, "joke_node")
    .add_edge("poem_node", "output_node")
    .add_edge("joke_node", "output_node")
    .add_edge("output_node", END)
)

with PostgresSaver.from_conn_string(PGSQL_URL) as checkpointer:
    checkpointer.setup()

    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {"thread_id": "langgraph_3_3_"},
    }

    # rprint(graph.invoke({"topic": "猫"}, config=config)["final_output"])

    rprint(list(graph.get_state_history(config)))  # 获取所有检查点状态
    rprint("-" * 50)
    rprint(graph.get_state(config))  # 获取【最新】的检查点状态
