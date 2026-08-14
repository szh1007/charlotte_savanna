from pathlib import Path

import dotenv
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model
from rich import print as rprint

"""
-- SKILL.md --
1.元数据 (name、description...) + 正文数据 (技能详细描述)
2.渐进式加载: 定义时只加载元数据, 真正调用时加载正文数据
3.定义技能时, 必须保持 文件名 = 技能名(元数据name)
4.必须配置 backend = FilesystemBackend, 因为技能本身是实体文件, 需要文件读取功能

Tips
1.早期纯提示词 --> 丰富性: 添加脚本 / 配置文件 / 各种静态资源
2.技能并非越多越好, 相同能力的技能不要放重复
3.有些技能可能需要【手动修改配置参数】, 要仔细阅读

"""
dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)

workspace_dir = Path(__file__).parent.resolve()
if not workspace_dir.exists():
    workspace_dir.mkdir(parents=True, exist_ok=True)

agent = create_deep_agent(
    model=model,
    tools=[],
    subagents=[],
    interrupt_on={},
    system_prompt="你是专业的智能助手, 可以使用backend进行长期记忆",
    backend=FilesystemBackend(workspace_dir, virtual_mode=True),
    skills=["/skills"],  # TODO: 技能所在的根文件夹, 路径是在 backend 路径的基础上
)

result = agent.invoke({"messages": [("user", "列举一下当前有哪些技能")]})
rprint(result["messages"][-1].content)
