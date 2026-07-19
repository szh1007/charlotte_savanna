import dotenv
from langchain.chat_models import init_chat_model
from langchain_core.utils.function_calling import convert_to_openai_tool
from rich import print as rprint

dotenv.load_dotenv()

llm = init_chat_model("deepseek:deepseek-v4-pro")


# 定义工具
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
llm_with_tools = llm.bind_tools([get_weather])

# 查看工具定义描述
rprint(convert_to_openai_tool(get_weather))

# 查看工具调用情况 tool_calls
rprint(llm_with_tools.invoke("深圳今天的天气").tool_calls)
