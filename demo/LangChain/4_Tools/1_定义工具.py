from langchain_core.tools import tool, StructuredTool
from pydantic import Field, BaseModel

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
    """ 如果 description 未定义, 则此处必须要有 docstring """
    return x + y


print(f"add name = {add.name}")  # 工具名称 (默认是函数名)
print(f"add args = {add.args}")  # 工具的参数列表 = 函数的参数列表
print(f"add description = {add.description}")  # 工具的功能描述 (默认是函数的 docstring)
print(f"add return_direct = {add.return_direct}")  # 是否直接将结果返回给用户 (默认是 False)
print("->", add.invoke({"x": 10, "y": 20}))


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

print(f"search name = {search.name}")
print(f"search args = {search.args}")
print(f"search description = {search.description}")
print(f"search return_direct = {search.return_direct}")
print("->", search.invoke({"query": "test"}))
