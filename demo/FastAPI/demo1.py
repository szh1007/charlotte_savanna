"""
启动方法1
$ uvicorn demo.FastAPI.demo1:app --reload

启动方法2
$ python demo/FastAPI/demo1.py

if __name__ == "__main__":
    uvicorn.run(app="demo1:app", host="127.0.0.1", port=8000, reload=True)

"""

from enum import Enum
from typing import Annotated

from fastapi import FastAPI, Form
from pydantic import BaseModel, Field, field_validator

app = FastAPI()


class Edu(str, Enum):
    UNIVERSITY = "university"
    GRADUATE = "graduate"
    PHD = "phd"


class User(BaseModel):
    name: str = Field("charlotte", min_length=2, max_length=10, description="用户名")
    age: int = Field(26, ge=18, le=60, description="年龄")
    pwd: str = Field(
        ...,
        pattern="^[a-zA-Z0-9]+$",
        min_length=12,
        max_length=18,
        description="密码",
    )
    edu: Edu = Field(Edu.UNIVERSITY, description="学历")
    skills: list[str] = Field(["LangChain", "FastAPI"], min_items=2, max_items=10, description="技能")
    desc: str | None = Field("", max_length=100, description="描述")

    @field_validator("skills", mode="before")
    @classmethod
    def parse_skills_form(cls, v: object) -> list[str]:
        """将 skills 统一标准化为 list[str]。

        输入可能是:
          - JSON 请求体: 已经是 list[str], 直接返回
          - 表单字符串: "LangChain,FastAPI" → ["LangChain", "FastAPI"]
          - FastAPI 包装后的列表: ["LangChain,FastAPI"] → 逐个元素拆分再展平
            (FastAPI 看到 list[str] 类型注解, 会将表单字符串自动包装为单元素列表)
        """
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]

        if isinstance(v, list):
            result: list[str] = []
            for item in v:
                if isinstance(item, str):
                    result.extend(s.strip() for s in item.split(",") if s.strip())
                else:
                    result.append(item)
            return result
        return v


@app.get("/user/{id}")
def test1(id: int, desc: str = ""):
    return {
        "code": 1,
        "message": "get user success",
        "data": {
            "id": id,
            "name": "charlotte",
            "desc": desc,
        },
    }


@app.post("/user/json")
def test2(user: User):
    """POST - JSON格式"""
    return {
        "code": 1,
        "message": "save user success",
        "data": user,
    }


@app.post("/user/form")
def test3(user: Annotated[User, Form()]):
    """POST - 表单格式"""
    return {
        "code": 1,
        "message": "save user success",
        "data": user,
    }
