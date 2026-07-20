import dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from pydantic import BaseModel, Field
from rich import print as rprint

dotenv.load_dotenv()

""" 大模型 LLM """
model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

""" 工具 Tools """


# 自定义工具
class GetWeatherFields(BaseModel):
    location: str = Field(description="城市名称")
    time: str = Field(description="时间", default="今天")


@tool(
    description="获取指定城市在指定时间的天气情况",
    args_schema=GetWeatherFields,
)
def get_weather(location: str, time: str = "今天") -> str:
    return f"{time}{location}在的天气: 多云转雷阵雨"


# 内置工具
tavily_search = TavilySearch(max_results=3)

tools = [get_weather, tavily_search]

""" 结构化输出 Pydantic """


class AgentOutputFields(BaseModel):
    question: str = Field(description="用户原始问题")
    answer: str = Field(description="AI回复内容")


class AgentMultiOutputFields(BaseModel):
    outputs: list[AgentOutputFields] = Field(description="助手的回复列表")


""" 消息 & 配置 - Messages & Config"""
messages = [
    ("system", "你是专业的智能AI助手, 你可以使用工具来回答用户的问题, 输出格式尽量简洁明确"),
    ("human", "2025年诺贝尔物理学奖的获奖者, 明天深圳的天气情况"),
]

config = {
    "recursion_limit": 10,
}

""" 智能体 Agent """
# React: 思考 - 行动 - 观察 - 思考 - ... - 观察 -> 输出
agent = create_agent(
    name="agent_assistant",
    model=model,
    tools=tools,
    system_prompt=None,
    response_format=ToolStrategy(
        schema=AgentMultiOutputFields,  # 结构化输出 Pydantic 模型
        tool_message_content="输出格式化成功!",  # 可以设置 Pydantic 模型用作 tool 调用后的 ToolMessage 的输出内容
        handle_errors=True,  # 默认捕获所有异常 (False 关闭自动重试 / string 预设固定字符串 / callable 自定义处理函数)
    ),
)

""" RUN"""
# invoke
response = agent.invoke({"messages": messages}, config=config)
rprint(response)
rprint("-" * 50)
rprint(response["messages"][-1].content)
rprint("-" * 50)
rprint(response["structured_response"])

# # stream
# # updates: 增量更新 (默认)                      - 思考与执行过程
# # messages: 信息流                              - 实时交互对话
# # values: 全量展示                              - 检查状态
# # tasks: 任务流, 包含各种监控信息                - 检查状态
# # debug: 调试流, 类似 tasks, 包含更多调试信息    - 检查状态
# for chunk in agent.stream({"messages": messages}, stream_mode="messages", config=config):
#     rprint(chunk)
#     rprint("-" * 50)

#     # if isinstance(chunk[0], AIMessage):
#     #     rprint(chunk[0].content, end="", flush=True)    # 无Pydantic + messages
