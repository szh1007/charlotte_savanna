from pathlib import Path

import dotenv
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model
from rich import print as rprint

"""
--- 被动记忆 (给 agent 使用)---
短期记忆: InMemorySaver / PostgresSaver
长期记忆: InMemoryStore / PostgresStore

--- 主动记忆 (给用户使用)---
长期记忆: *后端系统 Beckend*
"""

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)

workspace_dir = Path("./deepagent_md").resolve()
if not workspace_dir.exists():
    workspace_dir.mkdir(parents=True, exist_ok=True)

agent = create_deep_agent(
    model=model,
    tools=[],
    subagents=[],
    interrupt_on={},
    system_prompt="你是专业的智能助手, 可以使用backend进行长期记忆",
    backend=FilesystemBackend(workspace_dir, virtual_mode=True),
    # deepagents 内置了一些文件处理工具
)

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "查询python的发展历史, 并将结果存储到python.md文件中",
            }
        ]
    }
)
rprint(result)
