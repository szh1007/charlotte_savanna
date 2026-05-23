""" 模型的调用 """
import os, dotenv
from langchain_openai import ChatOpenAI

""" 1.模型调用的分类 """

# 1.按照模型的功能
# 非对话模型: LLMs、Text Moel
# 对话模型: Chat Model (推荐)
#   输入: List[BaseMessage] (SystemMessage, HumanMessage, AIMessage...)
#   输出: AIMessage
# 嵌入模型: Embedding Models (RAG使用)

# 2.按照模型调用时, 参数的位置 (api_key、model_name、base_uel等)
# 硬编码
# 环境变量
# 配置文件 (推荐)

# 3.具体API的调用
# 使用 LangChain 提供的 API (推荐)
# 使用 OpenAI 官方的 API
# 使用其他平台提供的 API

# # exp1 LangChain API + 对话模型 + 硬编码
# chat_model = ChatOpenAI(
#     model="gpt-4o-mini",
#     base_url="https://api.openai-proxy.org/v1",
#     api_key=SecretStr("sk-1jmKw77Y1M3xn8BYRff0MFkXheRiv8FZCWv6ONnLqpv5rxjy"),
# )
# response = chat_model.invoke("什么是LangChain?")
# print(response.content)

# # exp2 LangChain API + 对话模型 + 环境变量
# chat_model = ChatOpenAI(
#     model="gpt-4o-mini",
#     base_url=os.environ["OPENAI_BASE_URL"],
#     api_key=SecretStr(os.environ["OPENAI_API_KEY"]),
# )
# response = chat_model.invoke("什么是LangChain?")
# print(response.content)

# exp3 LangChain API + 对话模型 + 配置文件 (SOTA)
dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

chat_model = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.8,  # 确定保守 0 ~ 多样创意 1 (平衡模式 0.8)
    max_tokens=100,  # 限制生成文本的最大长度, 防止输出过长 (常规对话 512 ~ 1024)
)
response = chat_model.invoke("什么是LangChain?")
print(response.content)
