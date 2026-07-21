import dotenv
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from rich import print as rprint

dotenv.load_dotenv()

llm = init_chat_model("deepseek:deepseek-v4-pro")

# # 使用初始化方法实例化
# template = ChatPromptTemplate(
#     [
#         ("system", "你是专业的智能AI助手"),
#         ("human", "{input}"),
#     ]
# )

# 使用 from_messages 方法实例化 (推荐)
template = ChatPromptTemplate.from_messages(
    # list[string]: ["你好", "{input}", ...]
    # list[dict]: [{"role": "system", "content": "你是专业的智能AI助手"}, {"role": "human", "content": "{input}"}, ...]
    # list[tuple]: [("system", "你是专业的智能AI助手"), ("human", "{input}"), ...]
    # list[BaseMessage]: [SystemMessage("你是专业的智能AI助手"), HumanMessage("你好"), ...]  # 无法使用变量
    # list[BaseMessagePromptTemplate]: [HumanMessagePromptTemplate.from_template("{input}"), ...]
    # list[ChatPromptTemplate]: [ChatPromptTemplate([("human", "{input}"), ...]), ...]
    [
        ("system", "你是一个专业的{role}, 擅长{skill}"),
        # ("placeholder", "{history}"),   # 消息占位符 - "placeholder"
        MessagesPlaceholder("history"),  # 消息占位符 - MessagesPlaceholder
        ("human", "{input}"),
    ]
).partial(role="智能AI助手", skill="数学分析")  # 部分变量预填充

prompt = template.invoke(
    {
        "history": [
            ("human", "1 + 1 = ?"),
            ("ai", "2"),
        ],
        "input": "我刚才问了什么, 然后一句话介绍你自己",
    }
)

rprint(llm.invoke(prompt).content)
