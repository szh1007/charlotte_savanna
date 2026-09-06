from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.embedding import embedding_client
from app.clients.es import es_client
from app.clients.mysql import dw_client, meta_client
from app.clients.qdrant import qdrant_client

"""
生命周期组件 lifespan

应用启动时, 开始接收请求之前, 执行一次 (yield 之前)
应用关闭时, 接收了大量请求后, 执行一次 (yield 之后)
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化客户端
    dw_client.init()
    meta_client.init()
    qdrant_client.init()
    es_client.init()
    embedding_client.init()

    yield

    # 释放资源
    await dw_client.close()
    await meta_client.close()
    await qdrant_client.close()
    await es_client.close()
