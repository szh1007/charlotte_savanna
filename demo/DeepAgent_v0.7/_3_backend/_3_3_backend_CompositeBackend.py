import os
from dataclasses import dataclass
from pathlib import Path

import dotenv
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from rich import print as rprint

"""
--- 主动记忆 (给用户使用)---
长期记忆: *后端系统 Beckend*
    CompositeBackend
        不是具体的存储位置
        本质是一个路由, 控制不同的地址存储到不同的backend
"""

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)

workspace_dir = Path("./deepagent_md").resolve()
if not workspace_dir.exists():
    workspace_dir.mkdir(parents=True, exist_ok=True)


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


store = InMemoryStore()

file_backend = FilesystemBackend(workspace_dir, virtual_mode=True)
store_backend = StoreBackend(namespace=get_namespace)
composite_backend_instance = CompositeBackend(
    default=file_backend, routes={"/store/": store_backend}
)

agent = create_deep_agent(
    model=model,
    tools=[],
    subagents=[],
    system_prompt="""
    你是专业的智能助手, 配置了composite_backend, 可以将核心信息主动存储到backend中
    普通文件: 直接以文件名形式存储
    重要文件: 写入`/store/`目录下, 保存到store的指定位置下
    """,
    context_schema=UserContext,
    interrupt_on={},
    backend=composite_backend_instance,
    store=store,
    checkpointer=InMemorySaver(),
)

config = {
    "configurable": {"thread_id": "deepagent_3_3_"},
}

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "查询python的发展历史, 结果存储到python.md文件中(重要文件)",
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
