import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})


@tool
def tool1(input: str) -> str:
    """测试工具1"""
    return f"test_tool1_{input}"


@tool
def tool2(input: str) -> str:
    """测试工具2"""
    return f"test_tool2_{input}"


@tool
def tool3(input: str) -> str:
    """测试工具3"""
    return f"test_tool3_{input}"


tools = [tool1, tool2, tool3]

agent = create_agent(
    model=model,
    tools=tools,
    checkpointer=InMemorySaver(),
    middleware=[
        HumanInTheLoopMiddleware(  # 工具调用-人工审核中间件 (approve / edit / reject / respond)
            interrupt_on={
                "tool1": True,  # 中断, 支持所有人工操作
                "tool2": False,  # 直接放行
                "tool3": {  # 中断, 仅支持部分人工操作
                    "allowed_decisions": ["approve", "reject"],
                    "description": "中断! 人工调用工具3时不可编辑",
                },
            },
            description_prefix="中断! 人工审核",
        ),
    ],
)

messages = [
    ("system", "你是专业的智能AI助手"),
    ("human", "帮我调用一下所有工具的并返回所有的输出"),
]

# 确保人工干预后依然在同一个会话中
config = {"configurable": {"thread_id": "1"}}

response1 = agent.invoke({"messages": messages}, config=config)
rprint(response1)
rprint("-" * 50)

# 中断后
decisions = {"decisions": []}
interrupts = response1.get("__interrupt__", [])
if interrupts:
    action_requests = interrupts[0].value["action_requests"]

    for act in action_requests:
        if act["name"] == "tool1":
            decisions["decisions"].append(
                {"type": "edit", "edited_action": {"name": "tool1", "args": {"input": "charlotte"}}}
            )
        if act["name"] == "tool3":
            decisions["decisions"].append(
                {
                    "type": "approve",
                }
            )

    response2 = agent.invoke(Command(resume=decisions), config=config)
    rprint(response2["messages"][-1].content)
