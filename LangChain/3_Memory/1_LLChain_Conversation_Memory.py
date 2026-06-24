""" LLChain Conversation Memory """
import os, dotenv

from langchain_classic.chains.conversation.base import ConversationChain
from langchain_classic.chains.llm import LLMChain
from langchain_classic.memory import (
    ConversationBufferMemory,
    ConversationBufferWindowMemory,
    ConversationTokenBufferMemory,
    ConversationSummaryMemory,
    ConversationSummaryBufferMemory,
    ConversationEntityMemory,
)
from langchain_classic.memory.prompt import ENTITY_MEMORY_CONVERSATION_TEMPLATE
from langchain_community.memory.kg import ConversationKGMemory
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

"""
1.PromptTemplate        执行时未存储
2.ChatPromptTemplate    执行时已存储
"""
template1 = ChatPromptTemplate.from_messages([
    ("system", "你是一个AI专家, 你的名字叫小智"),
    MessagesPlaceholder("chat_history"),
    ("human", "{question}"),
])

template2 = ChatPromptTemplate.from_messages([
    ("system", "你是一个AI专家, 你的名字叫小智, 已知对话历史: {history}"),
    ("human", "{input}"),
])

"""
InMemoryChatMessageHistory
"""
# history = InMemoryChatMessageHistory()
# history.add_user_message("计算 5!")
# history.add_ai_message("120")
# history.add_user_message("我刚才的问题是什么")
# print(history.messages)
# print(model.invoke(history.messages).content)


"""
1.初始写法 (memory_key 默认为 "history", 可自定义)
"""
# memory = ConversationBufferMemory(return_messages=True, memory_key="chat_history")
# llm_chain = LLMChain(llm=model, prompt=template1, memory=memory)
# print(llm_chain.invoke({"question": "计算 5!"})["text"])
# print(llm_chain.invoke({"question": "我刚才的问题是什么"})["text"])

"""
1.1 保留最近记录
"""
# window_memory = ConversationBufferWindowMemory(k=1, return_messages=True, memory_key="chat_history")
# llm_chain = LLMChain(llm=model, prompt=template1, memory=window_memory)
# print(llm_chain.invoke({"question": "你好我叫 Charlotte"})["text"])
# print(llm_chain.invoke({"question": "我的女朋友叫 Savanna"})["text"])
# print(llm_chain.invoke({"question": "我叫什么名字"})["text"])

"""
1.2 保留最近 token (max_token_limit 默认 2000)
"""
# token_memory = ConversationTokenBufferMemory(llm=model, max_token_limit=25)
# token_memory.save_context({"input": "你好我叫 Charlotte"}, {"output": "你好, Charlotte"})
# token_memory.save_context({"input": "我的女朋友叫 Savanna"}, {"output": "好的, 我知道了"})
# print(token_memory.load_memory_variables({}))

"""
1.3 保存历史摘要 (chat_memory 可选)
"""
history = InMemoryChatMessageHistory()
history.add_user_message("我是AI专业毕业的研究生")
history.add_ai_message("好的，我知道了")
summary_memory = ConversationSummaryMemory.from_messages(llm=model, chat_memory=history)
summary_memory.save_context({"input": "你好, 我叫 Charlotte"}, {"output": "你好, Charlotte"})
summary_memory.save_context({"input": "我的女朋友叫 Savanna"}, {"output": "好的, 我知道了"})
print(summary_memory.load_memory_variables({}))

"""
1.4 混合记忆 (按 token, 最近记录保留、较早的记录摘要)
"""
# mix_memory = ConversationSummaryBufferMemory(llm=model, max_token_limit=30)
# mix_memory.save_context({"input": "你好, 我叫 Charlotte"}, {"output": "你好, Charlotte"})
# mix_memory.save_context({"input": "我的女朋友叫 Savanna"}, {"output": "好的, 我知道了"})
# mix_memory.save_context({"input": "我是AI专业毕业的研究生"}, {"output": "好的，我知道了"})
# print(mix_memory.load_memory_variables({}))

"""
2.简化写法: ConversationChain, 默认变量: history、input、response
"""
# chain = ConversationChain(llm=model, prompt=template2)
# print(chain.invoke({"input": "计算 5!"})["response"])
# print(chain.invoke({"input": "我刚才的问题是什么"})["response"])

"""
3.极简写法: ConversationChain 有默认的提示词模板, 默认变量: history、input、response
"""
# chain = ConversationChain(llm=model)
# print(chain.invoke({"input": "计算 5!"})["response"])
# print(chain.invoke({"input": "我刚才的问题是什么"})["response"])

"""
# 4.其他不常用的记忆 (简单了解)
"""
# # 1 实体记忆
# entity_memory = ConversationEntityMemory(llm=model)
# llm_chain = LLMChain(llm=model, prompt=ENTITY_MEMORY_CONVERSATION_TEMPLATE, memory=entity_memory)
# llm_chain.invoke({"input": "你好, 我叫 Charlotte"})
# llm_chain.invoke({"input": "我的女朋友叫 Savanna"})
# print(llm_chain.memory.entity_store.store)

# # 2 知识图谱记忆
# kg_memory = ConversationKGMemory(llm=model)
# kg_memory.save_context({"input": "你好, 我叫 Charlotte"}, {"output": "你好, Charlotte"})
# kg_memory.save_context({"input": "我的女朋友叫 Savanna"}, {"output": "好的, 我知道了"})
# print(kg_memory.load_memory_variables({"input": "Savanna 是谁"}))
# print(kg_memory.get_knowledge_triplets("Charlotte 喜欢玩剧本杀"))
