"""
安装Milvus (powershell)
Invoke-WebRequest \
    http://raw.githubusercontent.com/milvus-io/milvus/refs/heads/master/scripts/standalone_embed.bat \
        -Outfile standalone.bat
"""

import os

import dotenv
from pymilvus import MilvusClient
from rich import print as rprint

dotenv.load_dotenv()

MILVUS_URL = os.getenv("MILVUS_URL", "")
MILVUS_DB_NAME = os.getenv("MILVUS_DB_NAME", "")
MILVUS_COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "")

client = MilvusClient(MILVUS_URL)

# 检查数据库是否存在, 不存在则创建
if MILVUS_DB_NAME not in client.list_databases():
    client.create_database(MILVUS_DB_NAME)

rprint(client.list_databases())

# 切换数据库
client.use_database(MILVUS_DB_NAME)

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
