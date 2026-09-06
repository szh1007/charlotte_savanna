import uvicorn
from fastapi import FastAPI, Request

from app.api.query_router import query_router
from app.core.context import request_id_ctx_var
from app.core.lifespan import lifespan

# 创建fastapi的实例
app = FastAPI(lifespan=lifespan)

# 添加路由 router
app.include_router(query_router)


# 定义中间件
@app.middleware("http")
async def add_request_context_var(request: Request, call_next):
    # 设置上下文变量 - 用户ID (用于日志记录)
    request_id_ctx_var.set("charlotte")  # 模拟传入用户ID, 实际需要根据业务场景获取
    # 请求目标
    response = await call_next(request)
    return response


if __name__ == "__main__":
    uvicorn.run(app="main:app", host="127.0.0.1", port=8200, reload=True)
