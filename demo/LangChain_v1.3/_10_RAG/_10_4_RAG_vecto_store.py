"""
安装Milvus (powershell)
d:
cd /Docker/Milvus

Invoke-WebRequest \
    https://github.com/milvus-io/milvus/releases/\
        download/v3.0.0/milvus-standalone-docker-compose.yml \
            -OutFile docker-compose.yml

docker compose up -d
"""

import os

import dotenv
from pymilvus import MilvusClient
from rich import print as rprint

dotenv.load_dotenv()

MILVUS_URL = os.getenv("MILVUS_URL", "")
MILVUS_DATABASE_NAME = os.getenv("MILVUS_DATABASE_NAME", "")
MILVUS_COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "")

client = MilvusClient(MILVUS_URL)

# 检查数据库是否存在, 不存在则创建
if MILVUS_DATABASE_NAME not in client.list_databases():
    client.create_database(MILVUS_DATABASE_NAME)

rprint(client.list_databases())

# 切换数据库
client.use_database(MILVUS_DATABASE_NAME)

# 检查集合是否存在, 存在则删除
if MILVUS_COLLECTION_NAME in client.list_collections():
    client.drop_collection(MILVUS_COLLECTION_NAME)

# 重新创建 collection
client.create_collection(
    MILVUS_COLLECTION_NAME,
    dimension=int(os.getenv("EMBEDDING_DIM", 3072)),
    metric_type="COSINE",
)

rprint(client.list_collections())
rprint(client.describe_collection(MILVUS_COLLECTION_NAME))
