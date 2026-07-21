import dotenv
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field
from rich import print as rprint

dotenv.load_dotenv()

llm = init_chat_model("deepseek:deepseek-v4-pro")


# 定义工具参数 args_schema
class GetWeatherFields(BaseModel):
    location: str = Field(description="[High Priority] The location to get the weather for.")
    time: str = Field(description="[High Priority] The time to get the weather for.", default="today")


@tool(
    name_or_callable="Get_Weather",
    description="[High Priority] Get the weather for a given location and time.",
    args_schema=GetWeatherFields,
    parse_docstring=True,  # 是否将 docstring 转换为工具描述
)
def get_weather(location: str, time: str = "today") -> str:
    """Get the weather for a given location and time. (convert docstring to tool description)

    Args:
        location (str): The location to get the weather for.
        time (str, optional): The time to get the weather for. Defaults to "today".

    Returns:
        str: The weather description for the given location and time.

    """
    return f"The weather in {location} on {time} is rainy."


# 绑定工具
# tool_choice = "none" 绝不调用 / "auto" 自动 / "required" 必须调用 (可强制调用指定的工具, 设置为工具名即可)
llm_with_tools = llm.bind_tools([get_weather], tool_choice="auto")

# 查看工具定义描述
rprint(convert_to_openai_tool(get_weather))

# 查看工具调用情况 tool_calls
rprint(llm_with_tools.invoke("ShenZhen Weather tomorrow").tool_calls)

# 1.工具描述要尽可能清晰
# 2.每个工具职责划分要边界清晰
# 3.处理工具调用失败 - Agent级使用Prompt安排重试 / 调用级重试@retry / 最好让工具返回字符串 / 异步调用
