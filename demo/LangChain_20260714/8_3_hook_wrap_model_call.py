import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, wrap_model_call
from langchain.chat_models import init_chat_model
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})


"""
wrap_model_call 应用场景: 拦截、重试、缓存模型调用、修改系统提示
"""

""" 装饰器 """


@wrap_model_call
def wrap_model_call_middleware(request: ModelRequest, handler) -> ModelResponse | None:
    """
    Args:
        request: 模型请求元数据
        handler: 调用模型的行为
    """
    request.messages[-1].content += "---> wrap_model_call_before <---"

    response = handler(request)

    response.result[-1].content += "---> wrap_model_call_after <---"

    return response


""" 类 """


class TestWrapModelCallMiddleware(AgentMiddleware):
    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | None:
        """
        Args:
            request: 模型请求元数据
            handler: 调用模型的行为
        """
        request.messages[-1].content += "---> wrap_model_call_before <---"

        response = handler(request)

        response.result[-1].content += "---> wrap_model_call_after <---"

        return response


agent = create_agent(
    name="agent_assistant",
    model=model,
    middleware=[
        wrap_model_call_middleware,
        TestWrapModelCallMiddleware(),
    ],
)

messages = [
    ("system", "你是专业的智能AI助手"),
    ("human", "你好, 我叫Charlotte"),
]

response = agent.invoke({"messages": messages})
rprint(response)
