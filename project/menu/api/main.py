import asyncio
import math
import os
from datetime import datetime

import dotenv
import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.responses import StreamingResponse

from ..agent.langchain import embedding_model, mysql_engine, user_question

dotenv.load_dotenv()

app = FastAPI()

redis_client = Redis.from_url(os.getenv("MENU_REDIS_URL", ""), decode_responses=True)

SPICE_TEXT_MAP = {0: "不辣", 1: "微辣", 2: "中辣", 3: "重辣"}


class ChatRequest(BaseModel):
    query: str = Field(..., description="用户查询")
    thread_id: str = Field(..., description="线程ID")


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    return StreamingResponse(
        content=user_question(
            request.query, request.thread_id
        ),  # 异步生成器, 逐块发送响应
        media_type="text/event-stream",  # 事件流媒体类型 (比如 application/json)
    )


class FAQItem(BaseModel):
    question: str = Field(..., description="问题")
    answer: str = Field(..., description="答案")


class FAQResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    query: str = Field(..., description="用户查询")
    suggestions: list[FAQItem] = Field(..., description="FAQ项列表")


async def _load_all_faq_items():
    """从 redis 中加载所有 faq_items"""
    pipeline = redis_client.pipeline()
    faq_keys = await redis_client.smembers("faq:all_items")

    for key in faq_keys:
        pipeline.hgetall(key)

    results = await pipeline.execute()
    return [{**item} for item in results]


async def _calculate_score(faq_question: str, query: str) -> float:
    """计算 FAQ 问题与用户查询的余弦相似度.

    将两个文本分别编码为 embedding 向量后计算余弦相似度. embedding 是阻塞
    网络调用, 用 ``asyncio.to_thread`` 转到线程池执行, 避免阻塞事件循环.

    Args:
        faq_question: FAQ 问题文本.
        query: 用户查询文本.

    Returns:
        float: 两个文本向量间的余弦相似度, 取值 ``[-1, 1]``.
    """
    query_vec, faq_vec = await asyncio.gather(
        asyncio.to_thread(embedding_model.embed_query, query),
        asyncio.to_thread(embedding_model.embed_query, faq_question),
    )

    dot = sum(a * b for a, b in zip(query_vec, faq_vec))
    norm_query = math.sqrt(sum(a * a for a in query_vec))
    norm_faq = math.sqrt(sum(b * b for b in faq_vec))

    # 空向量兜底, 避免除零
    if norm_query == 0.0 or norm_faq == 0.0:
        return 0.0

    return dot / (norm_query * norm_faq)


@app.post("/faq/suggest", response_model=FAQResponse)
async def faq_endpoint(request: ChatRequest):
    topk = 2
    query = request.query

    # 加载所有的 faq_items
    faq_items = await _load_all_faq_items()

    # 将所有的 faq_items 和用户的 query 进行相似度计算
    for item in faq_items:
        item["score"] = await _calculate_score(item["question"], query)

    # 将所有的 faq_items 按相似度排序
    faq_items.sort(key=lambda x: x["score"], reverse=True)

    # 取相似度最高的 topk 结果
    suggestions = [FAQItem(**item) for item in faq_items[:topk]]

    return FAQResponse(
        success=True,
        query=query,
        suggestions=suggestions,
    )


@app.get("/health")
async def health_check():
    """健康检查, 附带数据库连通性状态。"""
    db_status = "ok"
    try:
        with mysql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/menu/list")
async def menu_list():
    """返回可售菜品列表。"""
    sql = """
        SELECT
            id, dish_name, price, description, category, spice_level,
            flavor, main_ingredients, cooking_method, is_vegetarian, allergens
        FROM menu_items
        WHERE is_available = 1
        ORDER BY id
    """
    with mysql_engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()

    items = []
    for row in rows:
        price = float(row["price"])
        spice_level = int(row["spice_level"] or 0)
        items.append(
            {
                "id": row["id"],
                "dish_name": row["dish_name"],
                "price": price,
                "formatted_price": f"¥{price:.2f}",
                "description": row["description"],
                "category": row["category"],
                "spice_level": spice_level,
                "spice_text": SPICE_TEXT_MAP.get(spice_level, "未知"),
                "flavor": row["flavor"],
                "main_ingredients": row["main_ingredients"],
                "cooking_method": row["cooking_method"],
                "is_vegetarian": bool(row["is_vegetarian"]),
                "allergens": row["allergens"],
            }
        )
    return {"menu_items": items}


@app.get("/reservation/list")
async def reservation_list(limit: int = Query(10, ge=1, le=100)):
    """返回最近的预约记录。"""
    sql = """
        SELECT
            id, num_people, num_children, arrival_time,
            seat_preference, main_dish_preference, other_comments, created_at
        FROM reservation_order
        ORDER BY id DESC
        LIMIT :limit
    """
    with mysql_engine.connect() as conn:
        rows = conn.execute(text(sql), {"limit": limit}).mappings().all()

    reservations = []
    for row in rows:
        arrival = row["arrival_time"]
        reservations.append(
            {
                "id": row["id"],
                "num_people": row["num_people"],
                "num_children": row["num_children"],
                "arrival_time": arrival.strftime("%Y-%m-%d %H:%M") if arrival else "",
                "seat_preference": row["seat_preference"],
                "main_dish_preference": row["main_dish_preference"],
                "other_comments": row["other_comments"],
            }
        )
    return {"reservations": reservations}


if __name__ == "__main__":
    uvicorn.run(
        "project.menu.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
