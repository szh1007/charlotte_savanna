import os
from dataclasses import dataclass

import dotenv
from deepagents import create_deep_agent
from deepagents.backends import StoreBackend
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from rich import print as rprint

"""
--- 主动记忆 (给用户使用)---
长期记忆: *后端系统 Beckend*
    StoreBackend
        主动记忆存储在 store 容器
        在 store中 使用 ns 区分被动长期记忆和主动长期记忆
"""

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)


@dataclass
class UserContext:
    name: str
    role: str


def get_namespace(runtime):
    # 可以使用 runtime 实现其他逻辑
    name = runtime.context.name
    role = runtime.context.role
    # thread_id = runtime.execution_info.thread_id
    user_ns = f"ns-{name}-{role}"
    rprint(f"指定命名空间: {user_ns}", "\n", "-" * 100)
    return ("filesystem", user_ns)


checkpointer = InMemorySaver()
store = InMemoryStore()
store_backend = StoreBackend(namespace=get_namespace)

agent = create_deep_agent(
    model=model,
    tools=[],
    subagents=[],
    system_prompt="你是专业的智能助手, 配置了store backend, 结果主动存储到backend中",
    context_schema=UserContext,
    interrupt_on={},
    backend=store_backend,  # 指定了主动记忆的存储方向 -> store -> 成为主动长期记忆
    store=store,
    checkpointer=checkpointer,
)

config = {
    "configurable": {"thread_id": "deepagent_3_2_"},
}

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "查询python的发展历史, 并将结果存储到python.md文件中",
            }
        ]
    },
    config=config,
    context=UserContext(name="charlotte", role="admin"),
)
rprint(result["messages"][-1].content)


result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "读取python.md文件内容, 并帮我简短总结一下",
            }
        ]
    },
    config=config,
    context=UserContext(name="charlotte", role="admin"),
)
rprint(result["messages"][-1].content)

items = store.search(("filesystem", "ns-charlotte-admin"))
for item in items:
    rprint(f"{item.key}: {item.value}")
