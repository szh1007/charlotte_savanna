import dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from pydantic import BaseModel, Field
from rich import print as rprint

dotenv.load_dotenv()

""" LLM """
model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

""" Tools """


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

""" Messages & Config"""
messages = [
    ("system", "你是专业的智能AI助手, 你可以使用工具来回答用户的问题, 输出格式尽量简洁明确"),
    ("human", "2025年诺贝尔物理学奖的获奖者, 明天深圳的天气情况"),
]

config = {
    "recursion_limit": 5,
}

""" Agent """
# React: 思考 - 行动 - 观察 - 思考 - ... - 观察 -> 输出
agent = create_agent(
    name="agent_assistant",
    model=model,
    tools=tools,
    system_prompt=None,
)

response = agent.invoke({"messages": messages}, config=config)
rprint(response)
rprint(response["messages"][-1].content)
