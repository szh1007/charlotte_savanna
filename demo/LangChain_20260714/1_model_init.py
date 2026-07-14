import os

import dotenv
from langchain.chat_models import init_chat_model
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()


# 方法1: ChatDeepSeek 自动获取指定环境变量
llm1 = ChatDeepSeek(model="deepseek-v4-pro")

# 方法2: ChatOpenAI 兼容多平台
llm2 = ChatOpenAI(
    model="deepseek-v4-pro",
    base_url=os.getenv("DEEPSEEK_API_BASE", ""),
    api_key=os.getenv("DEEPSEEK_API_KEY", ""),
)

# 方法3: init_chat_model 根据供应商自动识别 (推荐)
llm3 = init_chat_model("deepseek:deepseek-v4-pro")

response = llm3.invoke("一句话介绍你自己")
# print(response.content)
