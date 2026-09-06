from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.query_schema import QuerySchema
from app.services.query import QueryService

# 创建API路由实例
query_router = APIRouter()


# 定义查询接口
@query_router.post("/api/query")
async def create_item(
    schema: QuerySchema,
    service: QueryService = Depends(get_query_service),
):
    return StreamingResponse(
        service.query_sse(schema.query),
        media_type="text/event-stream",
    )
