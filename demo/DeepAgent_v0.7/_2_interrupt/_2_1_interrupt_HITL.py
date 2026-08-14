import dotenv
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)

"""
allowed_decision: approve、edit、reject
"""


@tool
def delete_datebase(table_name: str) -> str:
    """
    删除数据库表的工具

    Args:
        table_name: 数据库表名

    Returns:
        删除结果
    """
    rprint(f"开始删除数据库: {table_name}")
    rprint("-" * 100)
    return f"成功删除数据库: {table_name}"


@tool
def select_data(table_name: str) -> str:
    """
    查询数据库表的工具, 无需审批

    Args:
        table_name: 数据库表名

    Returns:
        查询结果
    """
    rprint(f"开始查询数据库表: {table_name}")
    rprint("-" * 100)
    return f"成功查询数据库表: {table_name}"


@tool
def delete_file(file_path: str) -> str:
    """
    删除文件的工具

    Args:
        file_path: 文件路径

    Returns:
        删除结果
    """
    rprint(f"开始删除文件: {file_path}")
    rprint("-" * 100)
    return f"成功删除文件: {file_path}"


agent = create_deep_agent(
    model=model,
    tools=[delete_datebase, select_data, delete_file],
    system_prompt="""
        角色: 专业的数据库助手
        功能:
            delete_datebase: 删除数据库表的工具, 高危操作, 需要审批
            select_data: 查询数据库表的工具, 普通操作, 无需审批
            delete_file: 删除文件的工具, 高危操作, 需要审批
        边界: 根据用户需求调用工具来完成任务
    """,
    interrupt_on={
        "delete_datebase": True,
        "delete_file": True,
        "select_data": {"allowed_decisions": ["edit"]},
    },
    checkpointer=InMemorySaver(),
)

config = {
    "configurable": {"thread_id": "deepagent_3_"},
}


messages = [
    (
        "user",
        "同时执行以下任务:\
            1.查询数据库表user的内容; \
            2.删除数据库表user; \
            3.无论文件是否存在都删除文件user.txt",
    )
]


def get_decisions(actions: list[dict]) -> list[dict]:
    decisions = []
    for action in actions:
        name = action["name"]
        if name == "select_data":
            decisions.append(
                {
                    "type": "edit",
                    "edited_action": {
                        "name": name,
                        "args": {"table_name": "user_business"},
                        "description": "重定向到查询数据库表user_business",
                    },
                }
            )
        if name == "delete_datebase":
            decisions.append({"type": "reject"})
        if name == "delete_file":
            decisions.append({"type": "approve"})
    return decisions


# # invoke
# interrupts = agent.invoke(
#     {"messages": messages},
#     config=config,
# ).get("__interrupt__")

# stream
interrupts = None
for chunk in agent.stream(
    {"messages": messages},
    config=config,
):
    if chunk.get("__interrupt__"):
        interrupts = chunk["__interrupt__"]
        break

# resume
if interrupts:
    rprint(interrupts)
    actions = interrupts[0].value["action_requests"]
    result = agent.invoke(
        Command(resume={"decisions": get_decisions(actions)}),
        config=config,
    )
    rprint(result["messages"][-1].content)
