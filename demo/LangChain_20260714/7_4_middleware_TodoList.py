import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.chat_models import init_chat_model

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

# 仅做定义上的展示
agent = create_agent(
    model=model,
    tools=[],
    middleware=[  # 任务规划 - 追踪进度 - 应对复杂多步任务
        TodoListMiddleware()  # 默认有内置工具 write_todos
    ],
    system_prompt="你是一个代码修复助手, 遇到多步骤问题时, 先用 write_todos 列出待办事项, 然后读取文件、修复代码",
)
