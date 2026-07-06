""" 模型的调用 """
import os, dotenv

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

"""
一、模型调用的分类
    1.按照模型的功能
        1.1 非对话模型: LLMs、Text Moel
          输入: str
          输出: str
        1.2 对话模型: Chat Model (原生支持多轮对话) (推荐)
          输入: str / List[BaseMessage] (SystemMessage, HumanMessage, AIMessage...)
          输出: AIMessages
        1.3 嵌入模型: Embedding Models (RAG使用)

    2.按照模型调用时, 参数的位置 (api_key、model_name、base_uel等)
        2.1 硬编码
        2.2 环境变量
        2.3 配置文件 (推荐)

    3.具体API的调用
        3.1 使用 LangChain 提供的 API (推荐)
        3.2 使用 OpenAI 官方的 API
        3.3 使用其他平台提供的 API
"""
# # 1.LangChain API + 对话模型 + 硬编码 (⚠️ 不安全，仅作对比演示)
# chat_model = ChatOpenAI(
#     model="gpt-4o-mini",
#     base_url="https://api.openai-proxy.org/v1",
#     api_key=SecretStr("sk-your-api-key"),
# )

# # 2.LangChain API + 对话模型 + 环境变量
# chat_model = ChatOpenAI(
#     model="gpt-4o-mini",
#     base_url=os.environ["OPENAI_BASE_URL"],
#     api_key=SecretStr(os.environ["OPENAI_API_KEY"]),
# )

# 3.LangChain API + 对话模型 + 配置文件 (SOTA)
dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

chat_model = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.8,  # 确定保守 0 ~ 多样创意 1 (平衡模式 0.8)
    max_tokens=512,  # 限制生成文本的最大长度, 防止输出过长 (常规对话 512 ~ 1024)
)

"""
二、对话模型的消息 Message
    1.字符串 str
    2.消息列表 List[BaseMessage] (SystemMessage, HumanMessage, AIMessage...)
"""
# # 1. invoke - 字符串 str
# response = chat_model.invoke("什么是LangChain?")
# print(response.content)

# # 2. invoke - 消息列表 List[BaseMessage] (SystemMessage, HumanMessage, AIMessage...)
# system_message = SystemMessage(content="你是一个Python编程专家")
# human_message = HumanMessage(content="帮我指定一份Python的学习计划")
# response = chat_model.invoke([system_message, human_message])
# print(response.content)

"""
三.多轮对话和上下文记忆
    1.大模型本身没有记忆
    2.单次对话有记忆, 多次对话相互独立
"""
# messages = [
#     SystemMessage(content="你是一个人工智能助手, 你的名字叫小智"),
#     HumanMessage(content="人工智能的英文缩写是什么"),
#     AIMessage(content="AI"),
#     HumanMessage(content="你叫什么名字, 我刚刚问你的问题和答案可以重复一下吗"),
# ]
# response = chat_model.invoke(messages)
# print(response.content)

"""
四.模型调用方法
    1.阻塞式 invoke(): 一次性返回所有结果
    2.流式 stream(): 一边生成、一边返回
    3.批量 batch(): 一次性请求多个messages
        messages_list = [messages1, messages2, messages3]
        response_list = chat_model.batch(messages_list)
        for res in response_list:   # 按顺序返回AIMessage列表
            print(res.content))
    4.异步 ainvoke() / astream() (了解)
"""
# 流式 stream()
stream_chat_model = ChatOpenAI(
    model="gpt-4o-mini",
    streaming=True,
)
messages = [HumanMessage(content="帮我制定一个英语六级的学习计划")]
for chunk in stream_chat_model.stream(messages):
    # 逐个打印内容块 (刷新缓冲区, 保证没有换行符的情况下, 内容能立即显示)
    print(chunk.content, end="", flush=True)
