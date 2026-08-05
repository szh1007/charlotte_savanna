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

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

PG_DB_URL = f"postgresql://{os.getenv('PG_DB_USERNAME', '')}:{os.getenv('PG_DB_PASSWORD', '')}\
    @{os.getenv('PG_DB_HOST', '')}:{os.getenv('PG_DB_PORT', '')}\
        /{os.getenv('PG_DB_NAME', '')}\
            ?sslmode=disable"


class OverAllState(MessagesState):
    topic: str = Field(description="主题")
    poem: str = Field(description="诗")
    joke: str = Field(description="笑话")
    final_output: str = Field(description="最终输出")


class InputState(MessagesState):
    topic: str = Field(description="主题")


class OutputState(MessagesState):
    final_output: str = Field(description="最终输出")


topics = ["美国短毛猫", "英国短毛猫", "布偶猫", "波斯猫"]
topic_idx = 0


def node_change_topic(state: InputState) -> OverAllState:
    global topic_idx
    logger.info(f"当前主题索引: {topic_idx}")
    sub_topic = topics[topic_idx]
    topic_idx = (topic_idx + 1) % len(topics)
    return {"topic": f"{state['topic']}-{sub_topic}"}


def poem_node(state: OverAllState) -> OverAllState:
    logger.info(f"开始写诗, 主题: {state['topic']}")
    poem = model.invoke([HumanMessage(f"写一首关于{state['topic']}的七言诗")]).content
    return {"poem": poem}


def joke_node(state: OverAllState) -> OverAllState:
    logger.info(f"开始写笑话, 主题: {state['topic']}")

    # 第1次运行: 模拟失败
    # 第2次运行: 注释掉 模拟BUG已修复 准备恢复检查点
    # if "猫" in state["topic"]:
    #     time.sleep(5)
    #     raise Exception("模拟失败")

    joke = model.invoke([HumanMessage(f"写一个关于{state['topic']}的笑话")]).content
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
    .add_node(node_change_topic)
    .add_node(poem_node)
    .add_node(joke_node)
    .add_node(output_node)
    .add_edge(START, "node_change_topic")
    .add_edge("node_change_topic", "poem_node")
    .add_edge("node_change_topic", "joke_node")
    .add_edge("poem_node", "output_node")
    .add_edge("joke_node", "output_node")
    .add_edge("output_node", END)
)

with PostgresSaver.from_conn_string(PG_DB_URL) as checkpointer:
    checkpointer.setup()

    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {"thread_id": "langgraph_3_4_"},
    }

    # 第1次运行: 模拟会话中断, 查看此时的检查点内容
    # rprint(graph.invoke({"topic": "猫"}, config=config)["final_output"])
    # rprint(list(graph.get_state_history(config)))

    # 第2次运行: 注释掉上面两行, 初始状态输入【None】, 从中断的状态【恢复】
    rprint(graph.invoke(None, config=config))
    # rprint(list(graph.get_state_history(config)))

    # 对于输入为【None】的解释
    # 1.如果上一次运行有中断, 则从中断的状态恢复, 并且会继续从中断处继续执行
    # 2.如果上一次运行没有中断, 则会执行【replay】, 把上一次的运行结果重新展示出来
    # 3.在2的前提下, 可以指定更细节的config, 比如某一个检查点的config, 不过效果上和2没差
