from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from rich import print as rprint

from .demo1 import User

app = FastAPI()


class SuccessResponse(BaseModel):
    code: int = 1
    message: str = "success"
    data: User = Field(..., description="用户信息")


class ErrorResponse(BaseModel):
    code: int = 0
    message: str = "error"


"""
响应类型: json / html / file
"""


@app.get("/test/json/{id}", response_model=SuccessResponse | ErrorResponse)
async def test1(id: int):
    if id == 1:
        return SuccessResponse(
            data=User(pwd="test12345678", desc="test success response")
        )
    else:
        return ErrorResponse()


@app.get("/test/html", response_class=HTMLResponse)
async def test2():
    return "<h1>Hello World</h1>"


@app.get("/test/file", response_class=FileResponse)
async def test3():
    return FileResponse("./demo/FastAPI/demo1.py")


"""
异常处理: HTTPException
"""


@app.get("/test/exception/{id}")
async def test4(id: int):
    valid_id_list = range(10)
    if id not in valid_id_list:
        raise HTTPException(status_code=404, detail="id not found")
    return {"id": id, "name": "charlotte"}


"""
中间件
执行顺序 (前后包裹结构): start 2 -> start 1 -> end 1 -> end 2
"""


@app.middleware("http")
async def middleware1(request, call_next):
    rprint("middleware1 start")
    response = await call_next(request)
    rprint("middleware1 end")

    return response


@app.middleware("http")
async def middleware2(request, call_next):
    rprint("middleware2 start")
    response = await call_next(request)
    rprint("middleware2 end")
    return response


"""
依赖注入
"""


async def pagenation(
    page: int = Query(1, gt=0, description="页码"),
    page_size: int = Query(10, ge=10, le=100, description="每页数量"),
):
    return {"page": page, "page_size": page_size}


async def permission(token: str = Header(..., description="token")):
    if not token.startswith("sk-") and not token.endswith("1007"):
        raise HTTPException(status_code=401, detail="token invalid")
    return {"user": "admin", "token": token}


router = APIRouter(dependencies=[Depends(permission)])  # 路由级依赖注入
# app = FastAPI(dependencies=[Depends(permission)])  # 全局级依赖注入


@app.get("/test/depends/pagenation/{id}")
async def test5(id: int, pagenation: dict = Depends(pagenation)):
    return {"id": id, "name": "charlotte", **pagenation}


# @app.get("/test/depends/permission", dependencies=[Depends(permission)])
@router.get("/depends/permission")
async def test6():
    return {"result": "welcome to Admin Dashboard"}


app.include_router(router, prefix="/test")
