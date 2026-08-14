from pathlib import Path

import dotenv
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model
from rich import print as rprint

"""
permission
不是控制 backend, 而是控制其生成的文件的权限
即控制 deepagent 后台的文件操作工具的权限
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
    permissions=[
        # 自上而下 顺序匹配
        # 如果无任何规则命中, 则默认允许所有读写
        # 规范要求: 具体路径在前, 宽泛全局在后
        # 模型正常执行返回权限执行的结果, 无法被try捕获
        FilesystemPermission(
            operations=["write"],  # 允许 /** 路径下的写操作
            paths=["/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=[  # 禁止 /** 路径下的读操作
                "read",
            ],
            paths=["/**"],
            mode="deny",
        ),
    ],
)

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "查询python的发展历史, 并将结果存储到python.md文件中",
            }
        ]
    },
)
rprint(result["messages"][-1].content)


result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": f"读取{workspace_dir}/python.md文件内容, 并帮我简短总结一下",
            }
        ]
    },
)
rprint(result["messages"][-1].content)
