import dotenv
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    after_agent,
    after_model,
    before_agent,
    before_model,
)
from langchain.chat_models import init_chat_model
from rich import print as rprint
from streamlit.runtime import Runtime

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

"""
* 装饰器参数 can_jump_to = ["end", "tool", "model"]
* eg: @before_model(can_jump_to=["end", "model"])

end: hook 执行完时, 允许直接跳转到 end 节点, 终止流程
tool: hook 执行完时, 允许跳转到 tool 节点, 开始调用工具
model: hook 执行完时, 允许重新跳转到 model 节点, 再次调用模型
"""


@before_model
def before_model_middleware(state: AgentState, runtime: Runtime):
    """
    Args:
        state (AgentState): Agent 状态, 包含消息列表
        runtime (Runtime): 上下文环境, 包含长期记忆
    """
    state["messages"][-1].content += "---> before_model <---"


@after_model
def after_model_middleware(state: AgentState, runtime: Runtime):
    state["messages"][-1].content += "---> after_model <---"


@before_agent
def before_agent_middleware(state: AgentState, runtime: Runtime):
    state["messages"][-1].content += "---> before_agent <---"


@after_agent
def after_agent_middleware(state: AgentState, runtime: Runtime):
    state["messages"][-1].content += "---> after_agent <---"


agent = create_agent(
    name="agent_assistant",
    model=model,
    middleware=[
        before_model_middleware,
        after_model_middleware,
        before_agent_middleware,
        after_agent_middleware,
    ],
)

messages = [
    ("system", "你是专业的智能AI助手"),
    ("human", "你好, 我叫Charlotte"),
]

response = agent.invoke({"messages": messages})
rprint(response)
