import json
import os

import dotenv
from langchain_community.tools import CopyFileTool, MoveFileTool
from langchain_core.messages import HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_function
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

"""
一、模型分析出可调用的工具
    1.如果分析出有需要调用的工具
        content: 内容为空, 因为模型需要调用工具, 所以就不会直接返回信息
        additional_kwargs: 包含 function_call 字段, 指明具体函数的名称和参数
    2.如果分析出没有需要调用的工具
        content: 直接返回内容结果
        additional_kwargs: 不包含 function_call 字段
"""
tools = {
    "move_file": MoveFileTool(),
    "copy_file": CopyFileTool(),
}
functions = [convert_to_openai_function(v) for _, v in tools.items()]

messages = [
    HumanMessage("把当前目录下的文件 1_定义工具.py 复制到 C:\\Users\\Lenovo\\Desktop"),  # 有相关的工具
    # HumanMessage("查一下今天深圳的天气"),  # 没有相关的工具
]

response = model.invoke(input=messages, functions=functions)

"""
二、调用模型分析出的工具
"""
tool_name, tool_args = "", {}
if "function_call" in response.additional_kwargs:
    function_call = response.additional_kwargs["function_call"]
    tool_name = function_call["name"]
    tool_args = json.loads(function_call["arguments"])  # str -> json
else:
    pass

if tool_name in tools:
    result = tools[tool_name].run(tool_args)
