import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage, HumanMessage, SystemMessage
from rich import print as fprint

dotenv.load_dotenv()

llm = init_chat_model(
    model="deepseek:deepseek-v4-pro",
    temperature=0.7,  # 0 ~ 2, 0 表示更一致、准确, 2 表示更随机、艺术
    max_tokens=1024,  # 最大输出长度, 0 表示不限制
    timeout=60,  # 超时时间, 单位秒
    max_retries=3,  # 最大重试次数, 0 表示不重试
)

""" invoke 阻塞式 """
# string
# print(llm.invoke("一句话介绍你自己").content)

# 字典列表
messages = [
    {"role": "system", "content": "你是专业的AI助手"},
    {"role": "user", "content": "1 + 1 = ?"},
    {"role": "assistant", "content": "2"},
    {"role": "user", "content": "我刚才问了什么问题?"},
]
# print(llm.invoke(messages).content)

# 消息对象列表
messages = [
    SystemMessage("你是专业的AI助手"),
    HumanMessage("1 + 1 = ?"),
    AIMessage("2"),
    HumanMessage("我刚才问了什么问题?"),
]
# print(llm.invoke(messages).content)

""" invoke return """
# fprint(llm.invoke(messages))    # AIMessage

""" stream 流式 """
# for chunk in llm.stream("详细介绍一下LangChain"):
#     fprint(chunk.text, end="", flush=True)

""" batch 批量处理 """
input_list = [
    "简单几句话介绍一下Python",
    "简单几句话介绍一下LangChain",
]
for resp in llm.batch(input_list):  # batch_as_completed 按照完成顺序
    fprint(resp.content)

"""
异步
1. ainvoke ...
2. astream ...
3. abatch  ...
"""
