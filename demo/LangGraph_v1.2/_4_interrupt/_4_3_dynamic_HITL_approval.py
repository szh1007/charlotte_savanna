from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, interrupt
from loguru import logger
from pydantic import Field
from rich import print as rprint

"""
--- 使用规范 ---
1.不要用 try 包裹 interrupt, 会导致无法中断, 因为中断本质就是个异常
2.不要改单节点内的多个 interrupt 的顺序
    每次恢复是【重新运行当前节点】, 而不是从中断处恢复
    重新运行节点时, 系统检测到之前的中断有被填充数据, 才继续向下执行的
3.不要在不确定的循环中使用 interrupt
4.不要在 interrupt() 中使用复杂类型, 仅推荐 string / json
5.interrupt 之前的操作必须时幂等的(多次 = 1次)
    原理同2, 因为之前的操作恢复时会重新运行一遍
    比如累加、遍历修改、数据库增删改等
"""

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


class OverAllState(MessagesState):
    topic: str = Field(description="主题")
    poem: str = Field(description="诗")
    is_approved: bool = Field(description="是否通过")
    review: str = Field(description="审批意见")


def approval_node(state: OverAllState) -> Command[Literal["llm_node", "default_node"]]:
    is_approved = interrupt("是否同意调用模型? (y/n): ")
    goto = "llm_node" if is_approved else "default_node"
    return Command(goto=goto, update={"is_approved": is_approved})


def llm_node(state: OverAllState) -> OverAllState:
    logger.info(f"开始写诗, 主题: {state['topic']}")
    poem = model.invoke([HumanMessage(f"写一个关于{state['topic']}的七言诗")]).content
    return {"poem": poem}


def default_node(state: OverAllState) -> OverAllState:
    return {"poem": "请求被拒绝"}


def review_node(state: OverAllState) -> OverAllState:
    review = interrupt(
        {
            "instruction": "请输入审批意见: ",
            "poem": state["poem"],
        }
    )
    return {"review": review}


graph = (
    StateGraph(state_schema=OverAllState)
    .add_node(approval_node)
    .add_node(llm_node)
    .add_node(default_node)
    .add_node(review_node)
    .add_edge(START, "approval_node")
    .add_edge("llm_node", "review_node")
    .add_edge("default_node", END)
    .add_edge("review_node", END)
).compile(
    checkpointer=InMemorySaver(),
)

config = {"configurable": {"thread_id": "langgraph_4_3_"}}

response = graph.invoke({"topic": "猫"}, config=config)
interrupt_prompt1 = response["__interrupt__"][0].value

# 是否通过
is_approved = input(interrupt_prompt1).strip().lower() == "y"
is_approved_response = graph.invoke(Command(resume=is_approved), config=config)
interrupt_prompt2 = is_approved_response["__interrupt__"][0].value

# 审批意见
interrupt_prompt2_detail = (
    f"{interrupt_prompt2['poem']}\n\n{interrupt_prompt2['instruction']}"
)
review = input(interrupt_prompt2_detail).strip()
review_response = graph.invoke(Command(resume=review), config=config)

rprint(review_response)
