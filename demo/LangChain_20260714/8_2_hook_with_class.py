import dotenv
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from rich import print as rprint
from streamlit.runtime import Runtime

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

"""
* 装饰器参数 can_jump_to = ["end", "tool", "model"]
* eg: @hook_config(can_jump_to=["end", "model"])

end: hook 执行完时, 允许直接跳转到 end 节点, 终止流程
tool: hook 执行完时, 允许跳转到 tool 节点, 开始调用工具
model: hook 执行完时, 允许重新跳转到 model 节点, 再次调用模型
"""


class TestMiddleware(AgentMiddleware):
    """
    测试中间件, 用于在模型调用前和后添加自定义逻辑

    Args:
        state (AgentState): Agent 状态, 包含消息列表
        runtime (Runtime): 上下文环境, 包含长期记忆
    """

    def before_model(self, state: AgentState, runtime: Runtime):
        state["messages"][-1].content += "---> before_model <---"

    def after_model(self, state: AgentState, runtime: Runtime):
        state["messages"][-1].content += "---> after_model <---"

    def before_agent(self, state: AgentState, runtime: Runtime):
        state["messages"][-1].content += "---> before_agent <---"

    def after_agent(self, state: AgentState, runtime: Runtime):
        state["messages"][-1].content += "---> after_agent <---"


agent = create_agent(
    name="agent_assistant",
    model=model,
    middleware=[
        TestMiddleware(),
    ],
)

messages = [
    ("system", "你是专业的智能AI助手"),
    ("human", "你好, 我叫Charlotte"),
]

response = agent.invoke({"messages": messages})
rprint(response)
