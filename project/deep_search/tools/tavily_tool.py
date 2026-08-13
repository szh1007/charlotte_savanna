import dotenv
from langchain.tools import tool
from tavily import TavilyClient

from ..api.monitor import monitor

dotenv.load_dotenv()

tavily = TavilyClient()


@tool
def network_search(query: str, max_results: int = 3) -> str:
    """
    专门进行联网检索信息的工具, 需要开源信息时使用此工具即可

    Args:
        query (str): 搜索查询
        max_results (int, optional): 最大返回结果数. 默认 3.

    Returns:
        str: 搜索结果的JSON字符串

    """
    monitor.report_tool(
        tool_name="联网搜索工具",
        args={"检索问题": query, "最大查询数量": max_results},
    )
    return tavily.search(query, max_results=max_results)
