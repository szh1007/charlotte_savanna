""" 输出解析器 OutputParser """
import os, dotenv

from langchain_core.output_parsers import (
    StrOutputParser,
    JsonOutputParser,
    XMLOutputParser,
)
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

input_str = "周星驰的所有参演电影的汇总信息"
input_dict = {"question": input_str}

"""
StrOutputParser
"""
str_parser = StrOutputParser()
# print(str_parser.invoke(model.invoke(input_str)))
# print("-" * 100)

"""
JsonOutputParser
"""
json_parser = JsonOutputParser()
json_template = PromptTemplate.from_template(
    template="回答用户的问题, 满足格式: {format1}, {format2}\n问题: {question}",
    partial_variables={
        "format1": json_parser.get_format_instructions(),
        "format2": "问题表示为 question, 答案表示为 answer",
    }
)

# # 方式1
# print(json_parser.invoke(model.invoke(json_template.invoke(input_dict))))
# print("-" * 100)

# # 方式2 chain (推荐)
# chain = json_template | model | json_parser
# print(chain.invoke(input_dict))
# print("-" * 100)

"""
XMLOutputParser
XML结果会自动解析成dict格式
"""
xml_parser = XMLOutputParser()
xml_template = (
    PromptTemplate
    .from_template("回答用户的问题, 满足格式: {format}\n问题: {question}")
    .partial(format=xml_parser.get_format_instructions())
)

chain = xml_template | model | xml_parser
print(chain.invoke(input_dict))
