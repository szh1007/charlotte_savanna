from enum import Enum

import dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field
from rich import print as rprint

dotenv.load_dotenv()

llm = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})


class Edu(str, Enum):
    BACHELOR = "本科"
    MASTER = "硕士"
    PHD = "博士"
    OTHER = "其他"


class Person(BaseModel):
    name: str = Field(description="姓名", min_length=2, max_length=10)
    age: int | None = Field(description="年龄", ge=22, le=50)  # 可选
    job: str = Field(default="AI应用工程师", description="工作")  # 默认值
    edu: Edu = Field(default=Edu.MASTER, description="学历")  # 枚举值


class PersonList(BaseModel):
    people: list[Person] = Field(description="人员列表")  # 列表、嵌套
    output: str = Field(description="大模型原始输出")


try:
    structured_llm = llm.with_structured_output(PersonList)
    response = structured_llm.invoke("我叫charlotte, 是一位AI应用工程师, 你好")
    rprint(response)
except Exception as e:
    rprint(e)
