from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, interrupt
from pydantic import Field

"""
--- LangSmith 调试 LangGraph ---

1.安装: pip install langgraph-cli[inmem]

2.配置: langgraph.json (graphs 中可配置多个图, 以不同 key 对应不同的 url value 即可)
```json
{
    "dependencies": ["."],
    "graphs": {
        "demo": "demo/LangGraph_v1.2/_5_LangSmith/demo.py:graph",
        "graph": "..."
    },
    "env": ".env"
}
```

3.运行: langgraph dev

Tips: graph无需配置长短期记忆, LangSmith 平台会自动处理
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
).compile()
