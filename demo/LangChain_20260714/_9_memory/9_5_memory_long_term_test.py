from typing import NotRequired

import dotenv
from langchain.agents import AgentState, create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.store.memory import InMemoryStore
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})


class CustomAgentState(AgentState):
    user_id: NotRequired[str]


@tool(parse_docstring=True)
def save_user(name: str, runtime: ToolRuntime) -> str:
    """
    Save user information to the store

    Args:
        name: The user's name
        runtime: The tool runtime providing access to state and store

    Returns:
        str: A message indicating the user was saved
    """
    namespace = ("Users",)
    key = runtime.state["user_id"]
    value = {"name": name}

    runtime.store.put(namespace, key, value)
    return f"Saved user {value} with id {key} to store"


@tool(parse_docstring=True)
def get_user(runtime: ToolRuntime) -> str:
    """
    Get user information from the store

    Args:
        runtime: The tool runtime providing access to state and store

    Returns:
        str: A message indicating the user was retrieved
    """
    namespace = ("Users",)
    key = runtime.state["user_id"]
    user = runtime.store.get(namespace, key)

    if user:
        return f"Retrieved user {user} with id {key} from store"
    else:
        return f"No user found with id {key}"


agent = create_agent(
    model=model,
    tools=[get_user, save_user],
    middleware=[
        # 使用中间件访问长期记忆 (核心就是找 runtime)
        # runtime (Runtime) -> runtime.store
        # wrap_model_call - request (ModelRequest) -> request.runtime.store
        # wrap_tool_call - request (ToolCallRequest) -> request.runtime.store
    ],
    store=InMemoryStore(),  # PostgresStore 使用方法类型, 仅定义方式不同, 这里就不做展示了
    state_schema=CustomAgentState,
    system_prompt="用户提及个人信息时, 可以使用工具保存用户信息; 如果用户询问个人信息时, 可以实用工具查询用户",
)

resonse1 = agent.invoke(
    {"messages": ("human", "你好, 我叫 charlotte, 我的女朋友叫 savanna"), "user_id": "user_1"},
)
rprint(resonse1)

resonse2 = agent.invoke(
    {"messages": ("human", "我叫什么, 我的女朋友叫什么"), "user_id": "user_1"},
)
rprint(resonse2)
