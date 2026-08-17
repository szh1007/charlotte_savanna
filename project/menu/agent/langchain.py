"""餐厅智能助手 Agent

基于 LangChain 构建餐厅订座与菜单查询智能体, 集成:
- MySQL: 存储菜单 (menu_items) 与预约订单 (reservation_order)
- Milvus: 存储菜品文本向量, 支持按口味做语义检索
- OpenAI 兼容的向量嵌入模型: 将文本编码为向量

本模块对外暴露三个供 Agent 调用的 @tool 工具:
- make_reservation: 座位预订
- user_flavor_search: 按用户口味语义检索菜品
- search_main_dishes: 查询特色主菜

注意: 本模块依赖 milvus.py 先运行, 完成 menu 数据库与 menu_items 集合的初始化.
"""

import asyncio
import json
import os
from datetime import datetime

import dotenv
import pymysql
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field
from pymilvus import MilvusClient
from pymysql.cursors import DictCursor
from rich import print as rprint
from sqlalchemy import create_engine, text

from .prompt.prompt import agent_config

dotenv.load_dotenv()

# 全局 checkpointer, 保证同一 thread_id 的对话历史跨请求保留
checkpointer = InMemorySaver()


# 菜单字段英文名 -> 中文名映射, 用于将数据库字段转换为用户可读的中文标签
menu_items_mapping = {
    "dish_name": "主菜名称",
    "price": "价格",
    "description": "描述",
    "category": "分类",
    "spice_level": "辣度等级",
    "flavor": "口味",
    "main_ingredients": "主要食材",
    "cooking_method": "烹饪方法",
    "is_vegetarian": "是否为素食",
    "allergens": "过敏信息",
}

# 向量嵌入模型 (OpenAI 兼容接口), 将文本编码为向量, 供 Milvus 语义检索使用
embedding_model = init_embeddings(
    model=os.getenv("CLOSEAI_EMBEDDING_MODEL", ""),
    api_key=os.getenv("CLOSEAI_API_KEY", ""),
    base_url=os.getenv("CLOSEAI_BASE_URL", ""),
)

# Milvus 向量数据库客户端, 切换到 menu 数据库 (需 milvus.py 先完成初始化)
milvus_client = MilvusClient(os.getenv("MENU_MILVUS_URL", ""))
milvus_client.use_database("menu")

# MySQL 连接引擎 (SQLAlchemy), 供订座工具写入订单; pool_size=10 复用连接池
mysql_engine = create_engine(
    "mysql+pymysql://"
    f"{os.getenv('MENU_MYSQL_USERNAME', '')}:{os.getenv('MENU_MYSQL_PASSWORD', '')}@"
    f"{os.getenv('MENU_MYSQL_HOST', '')}:{os.getenv('MENU_MYSQL_PORT', '')}/"
    f"{os.getenv('MENU_MYSQL_NAME', '')}",
    pool_size=10,
)


class ReservationToolArgsInfo(BaseModel):
    """订座工具的入参 Schema.

    通过 ``args_schema`` 传给 @tool 装饰器, LangChain 据此校验入参,
    并将各字段的 ``description`` 作为工具参数说明暴露给模型, 引导正确传参.
    """

    num_people: int = Field(description="预约的总人数")
    num_children: int = Field(description="预约的 0-2 岁儿童人数")
    arrival_time: str = Field(description="预约的到达时间, 格式: YYYY-MM-DD HH")
    seat_preference: str = Field(
        description="预约的座位偏好, 当用户没有特殊需求时, 传递空字符串即可"
    )
    main_dish_preference: str = Field(
        description="预约的主菜偏好, 当用户没有特殊需求时, 传递空字符串即可"
    )
    comment: str = Field(
        description="预约的其他备注, 当用户没有特殊需求时, 传递空字符串即可"
    )


@tool(args_schema=ReservationToolArgsInfo)
def make_reservation(
    num_people: int,
    num_children: int,
    arrival_time: str,
    seat_preference: str,
    main_dish_preference: str,
    comment: str,
) -> str:
    """预订餐厅座位.

    将用户的预约信息写入 MySQL 的 reservation_order 表. 当用户明确表达
    预订意向且信息已二次确认后调用; 缺失的偏好信息传空字符串即可.

    Args:
        num_people: 预约的总人数.
        num_children: 0-2 岁儿童人数.
        arrival_time: 到达时间, 格式 ``YYYY-MM-DD HH``.
        seat_preference: 座位偏好, 无特殊需求传空字符串.
        main_dish_preference: 主菜偏好, 无特殊需求传空字符串.
        comment: 其他备注, 无特殊需求传空字符串.

    Returns:
        str: 预订结果提示语, 成功时返回 ``"预订成功"``.
    """
    with mysql_engine.connect() as conn:
        sql = """
            INSERT INTO reservation_order
            (num_people, num_children, arrival_time, seat_preference, main_dish_preference, other_comments)
            VALUES
            (:num_people, :num_children, :arrival_time, :seat_preference, :main_dish_preference, :other_comments)
        """  # noqa: E501

        params = {
            "num_people": num_people,
            "num_children": num_children,
            "arrival_time": arrival_time,
            "seat_preference": seat_preference,
            "main_dish_preference": main_dish_preference,
            "other_comments": comment,
        }

        # 使用命名参数绑定 (参数化查询), 避免 SQL 注入
        conn.execute(statement=text(sql), parameters=params)
        conn.commit()
        return "预订成功"


@tool
def user_flavor_search(query: str) -> list[str]:
    """根据用户口味语义检索匹配的菜品.

    将用户的口味描述编码为向量, 在 Milvus 的 menu_items 集合中做近似最近邻
    检索, 返回语义最接近的菜品文本列表.

    Args:
        query: 用户的口味描述或饮食偏好, 例如 ``"我喜欢西兰花, 营养丰富"``.

    Returns:
        list[str]: 与口味最匹配的菜品文本列表; 无匹配时返回提示语列表.
    """
    query_vector = embedding_model.embed_query(query)

    search_results = milvus_client.search(
        collection_name="menu_items",
        data=[query_vector],
        anns_field="vector",
        output_fields=["text"],
        limit=2,
    )

    if not search_results:
        return ["当前库中没有用户喜好相关的菜品"]

    # search 结果按查询向量分组, 这里只有一个查询向量, 取第一组
    results = search_results[0]

    return [item["entity"]["text"] for item in results]


@tool
def search_main_dishes() -> list[dict]:
    """查询菜单中所有特色主菜.

    从 MySQL 的 menu_items 表读取 ``is_featured=1`` 的记录, 并将字段名映射为
    中文标签后返回, 供模型理解并转述给用户.

    Returns:
        list[dict]: 特色主菜列表, 每项为 "中文标签 -> 字段值" 的字典.
    """
    with (
        pymysql.connect(
            host=os.getenv("MENU_MYSQL_HOST"),
            port=int(os.getenv("MENU_MYSQL_PORT")),
            user=os.getenv("MENU_MYSQL_USERNAME"),
            password=os.getenv("MENU_MYSQL_PASSWORD"),
            database=os.getenv("MENU_MYSQL_NAME"),
        ) as conn,
        conn.cursor(DictCursor) as cursor,
    ):
        sql = """
            select
                dish_name,
                price,
                description,
                category,
                spice_level,
                flavor,
                main_ingredients,
                cooking_method,
                is_vegetarian,
                allergens
            from
                menu_items
            where
                is_featured=1
        """
        cursor.execute(sql)
        results = cursor.fetchall()

        # 将英文字段名转换为中文标签, 便于模型理解和向用户展示
        return [
            {menu_items_mapping[key]: value for key, value in item.items()}
            for item in results
        ]


async def create_menu_agent():
    """创建餐厅助手 Agent.

    使用 DeepSeek 模型 (关闭 thinking 模式) 并挂载全部三个工具:
    特色主菜查询, 口味语义检索, 座位预订.

    Returns:
        组装好的 LangChain Agent 实例.
    """
    model = init_chat_model(
        model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
        extra_body={"thinking": {"type": "disabled"}},
    )

    return create_agent(
        name="agent_assistant",
        model=model,
        system_prompt=agent_config["system_prompt"],
        tools=[search_main_dishes, user_flavor_search, make_reservation],
        checkpointer=checkpointer,
    )


async def test_agent():
    agent = await create_menu_agent()
    config = {"configurable": {"thread_id": "project_menu_agent_langchain_test"}}
    result = await agent.ainvoke({"messages": "你能为我做什么"}, config=config)
    rprint(result["messages"][-1].content)


async def user_question(query: str, thread_id: str):
    """
    处理用户问题, 并返回模型回复.
    """
    agent = await create_menu_agent()
    config = {"configurable": {"thread_id": thread_id}}

    # 调用前增加 system_prompt, 让agent感知当前的时间
    current_date = datetime.now().strftime("%Y-%m-%d")
    time_system_prompt = ("system", f"当前日期是 {current_date}")

    messages = [time_system_prompt, ("human", query)]

    reservation_created = False

    async for chunk in agent.astream(
        {"messages": messages}, config=config, stream_mode="messages"
    ):
        msg = chunk[0]

        # 检测座位预订工具执行成功, 通知前端刷新预订列表
        if (
            not reservation_created
            and isinstance(msg, ToolMessage)
            and getattr(msg, "name", None) == "make_reservation"
            and getattr(msg, "status", None) == "success"
        ):
            reservation_created = True
            reservation_payload = {"type": "reservation_created"}
            reservation_payload_str = json.dumps(
                reservation_payload, ensure_ascii=False
            )
            yield f"data: {reservation_payload_str}\n\n"

        if not isinstance(msg, AIMessage):
            continue

        # SSE (Server-Sent Events) -> data: {"type": "token", "content": "..."}
        payload = {"type": "token", "content": msg.content}
        payload_str = json.dumps(payload, ensure_ascii=False)
        yield f"data: {payload_str}\n\n"


if __name__ == "__main__":
    # rprint(search_main_dishes.invoke({}))
    # rprint(user_flavor_search.invoke({"query": "我喜欢西兰花, 适合减肥人群"}))

    # asyncio.run(test_agent())
    asyncio.run(user_question("你能为我做什么", "project_menu_agent_langchain"))
