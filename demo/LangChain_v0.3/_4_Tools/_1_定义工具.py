from langchain_core.tools import StructuredTool, tool
from pydantic import BaseModel, Field

"""
@tool 装饰器定义
"""


class AddToolField(BaseModel):
    x: int = Field(description="参数1")
    y: int = Field(description="参数2")


@tool(
    name_or_callable="test_tool",
    args_schema=AddToolField,
    description="test description",
    return_direct=True,
)
def add(x: int, y: int) -> int:
    """如果 description 未定义, 则此处必须要有 docstring"""
    return x + y


"""
StructuredTool from_function() 可配置性更高
"""


class SearchToolField(BaseModel):
    query: str = Field(description="查询关键词")


def search_google(query: str) -> str:
    return f"{query}_result"


search = StructuredTool.from_function(
    func=search_google,
    name="Search Google",
    args_schema=SearchToolField,
    description="查询谷歌浏览器并将结果返回",
    return_direct=True,
)
