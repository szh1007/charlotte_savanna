import os

import dotenv
from langchain_core.tools import tool
from ragflow_sdk import RAGFlow
from rich import print as rprint

from ..api.monitor import monitor

"""
https://ragflow.io/docs/python_api_reference#list-chat-assistants
https://ragflow.io/docs/python_api_reference#session-management

工具1: 检索RAGFlow中有哪些提问助手
    获取【所有助手】的【名称、描述、关联的知识库】

工具2: 向RAGFlow中的指定助手提问
    1.根据助手的名称查询指定助手
    2.在助手中【创建会话】
    3.在会话中【提问】获取结果
    4.最后要【删除会话】
"""


def config_ragflow_env() -> tuple[str, str]:
    """
    加载RAGFlow的环境变量 (base_url, api_key)
    优先加载当前脚本目录下的.env文件, 若不存在则加载系统环境变量
    """
    dotenv.load_dotenv(override=True)

    api_key = os.getenv("DS_RAGFLOW_API_KEY")
    base_url = os.getenv("DS_RAGFLOW_API_URL")
    return base_url, api_key


@tool
def show_chat_list():
    """
    检索RAGFlow中有哪些提问助手
    为后续向具体的助手提问提供助手名称

    Args:
        None

    Returns:
        如果有数据 -> 返回 所有助手的名称、描述、关联的知识库的详细信息
        如果没有数据 -> 返回 "当前RAGFlow中没有任何聊天助手可用"
        访问报错 -> 返回 "RAGFlow聊天助手名称列表查询失败, 错误信息: ..."
    """
    monitor.report_tool(tool_name="RAGFlow聊天助手名称列表查询")

    try:
        base_url, api_key = config_ragflow_env()
        ragflow = RAGFlow(base_url=base_url, api_key=api_key)
        chat_list = ragflow.list_chats()
        if not chat_list:
            return "当前RAGFlow中没有任何聊天助手可用"

        result_str = ""
        for chat in chat_list:
            result_str += f"名称: {chat.name}, "
            result_str += f"描述: {chat.description}, "
            result_str += (
                f"关联知识库: "
                f"{', '.join([dataset['name'] for dataset in chat.datasets])}\n"
            )
        return result_str
    except Exception as e:
        return f"RAGFlow聊天助手名称列表查询失败, 错误信息: {e!s}"


@tool
def create_session_ask(chat_name: str, question: str):
    """
    向RAGFlow中的指定助手提问
    通过show_chat_list工具确认的名称和功能点

    Args:
        chat_name: 提问助手的助手名称
        question: 具体提问的问题

    Returns:
        如果有数据 -> 返回 助手的最终回答
        如果没有数据 -> 返回 f"RAGFlow中不存在助手: {chat_name}, 请重新核实"
        访问报错 -> 返回 f"RAGFlow聊天助手回答失败: {chat_name}, 错误信息: ..."
    """
    monitor.report_tool(
        tool_name="向指定助手提问", args={"助手": chat_name, "问题": question}
    )

    try:
        base_url, api_key = config_ragflow_env()
        ragflow = RAGFlow(base_url=base_url, api_key=api_key)

        # 1.根据助手的名称查询指定助手
        chat_list = ragflow.list_chats(name=chat_name)
        if not chat_list:
            return f"RAGFlow中不存在助手: {chat_name}, 请重新核实"

        # 2.在助手中【创建会话】
        chat = chat_list[0]
        session = chat.create_session(name="临时会话")

        # 3.在会话中【提问】获取结果
        stream = session.ask(question=question, stream=True)
        final_result = ""
        for chunk in stream:  # stream 已自动累加
            final_result = chunk.content

        # 4.最后要【删除会话】
        chat.delete_sessions([session.id])
        return final_result
    except Exception as e:
        return f"RAGFlow聊天助手回答失败: {chat_name}, 错误信息: {e!s}"


if __name__ == "__main__":
    rprint(show_chat_list())
    rprint(create_session_ask(chat_name="综合对话助手", question="中国刑法包含几部分"))
