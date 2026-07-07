"""
LCEL (SOTA)
"""
import os, dotenv

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

parser = JsonOutputParser()

template = (
    ChatPromptTemplate
    .from_messages([
        ("system", "回答用户的问题, 回复格式为: {format1}, {format2}"),
        ("human", "{question}"),
    ])
    .partial(
        format1=parser.get_format_instructions(),
        format2="key 用英文",
    )
)

chain = template | model | parser
result = chain.invoke({"question": "周星驰所有参演电影的汇总信息"})
print(result)
