from dataclasses import dataclass

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.runtime import Runtime
from loguru import logger
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


@dataclass
class UserContext:
    username: str
    membership_lv: str


class OverAllState(MessagesState):
    user_input: str = Field(description="用户输入")
    output: str = Field(description="模型输出")


def llm_node(state: OverAllState, runtime: Runtime[UserContext]) -> OverAllState:
    context = runtime.context
    username, level = context.username, context.membership_lv
    logger.info(f"当前用户: {username},用户等级: {level}")

    if level in ["lv.5", "lv.6"]:
        system_prompt = SystemMessage(
            content=(
                "你是高级客户助理, 回答问题时语气热情周到, 并多多询问用户的体验感受"
            )
        )
    else:
        system_prompt = SystemMessage(
            content=("你是普通客户助理, 回答问题时语气简洁理性, 简单直接陈述事实即可")
        )

    messages = state.get("messages", [])
    messages = [system_prompt, *messages, HumanMessage(state["user_input"])]

    response = model.invoke(messages).content
    return {"messages": [*messages, response], "output": response}


graph = (
    StateGraph(state_schema=OverAllState, context_schema=UserContext)
    .add_node(llm_node)
    .add_edge(START, "llm_node")
    .add_edge("llm_node", END)
).compile()

rprint(
    graph.invoke(
        {"user_input": "介绍一下你自己"},
        context=UserContext(username="Charlotte", membership_lv="lv.6"),
    ),
)

rprint(
    graph.invoke(
        {"user_input": "介绍一下你自己"},
        context=UserContext(username="Charlotte", membership_lv="lv.2"),
    ),
)
