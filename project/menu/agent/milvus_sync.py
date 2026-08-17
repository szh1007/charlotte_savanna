"""Milvus 向量库初始化脚本.

将 MySQL 中的菜品数据读取出来, 编码为向量后写入 Milvus 的 menu 数据库,
供 langchain.py 中的口味语义检索工具 (user_flavor_search) 使用.

执行流程:
1. 从 MySQL 读取 menu_items 表全部菜品, 转换为 "中文标签: 值" 格式的文本
2. 创建 Milvus 的 menu 数据库 (若不存在)
3. 创建 menu_items 集合 (含 id/vector/text 字段, 向量维度 3072)
4. 用嵌入模型将文本批量编码为向量并写入集合

注意: 本脚本为一次性初始化脚本, 每次运行会重建 menu_items 集合并写入最新数据.
"""

import os
from decimal import Decimal

import dotenv
import pymysql
from langchain.embeddings import init_embeddings
from pymilvus import DataType, IndexType, MilvusClient
from pymysql.cursors import DictCursor
from rich import print as rprint

dotenv.load_dotenv()

# 菜单字段英文名 -> 中文名映射 (与 langchain.py 保持一致)
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

# 从 MySQL 读取全部菜品, 转换为 "中文标签: 值" 格式的文本列表, 供嵌入模型编码
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
    """
    cursor.execute(sql)
    results = cursor.fetchall()

    str_list = []
    for item in results:
        str_item = ""
        for key, value in item.items():
            # Decimal 类型无法被嵌入模型直接处理, 统一转为 float
            if isinstance(value, Decimal):
                value = float(value)
            str_item += f"{menu_items_mapping[key]}: {value}\n"
        str_list.append(str_item)

milvus_client = MilvusClient(os.getenv("MENU_MILVUS_URL", ""))

# 确保 menu 数据库存在 (首次运行自动创建)
if "menu" not in milvus_client.list_databases():
    milvus_client.create_database("menu")

milvus_client.use_database("menu")

# 定义 menu_items 集合的字段 Schema:
# - id: 自增主键 (auto_id 由 Milvus 自动生成)
# - vector: 文本向量, 维度 3072 (由嵌入模型输出维度决定)
# - text: 原始文本, 最大长度 1024
schema = (
    milvus_client.create_schema(
        auto_id=True,
    )
    .add_field(
        field_name="id",
        datatype=DataType.INT64,
        is_primary=True,
    )
    .add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=3072)
    .add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=1024,
    )
)

# 为 vector 字段建立 HNSW 索引, 采用余弦相似度度量
index_params = MilvusClient.prepare_index_params()
index_params.add_index(
    field_name="vector",
    index_type=IndexType.HNSW,
    metric_type="COSINE",
)

# 重建集合: 已存在则先删除, 保证每次运行写入干净的最新数据
if "menu_items" in milvus_client.list_collections():
    milvus_client.drop_collection("menu_items")

collection = milvus_client.create_collection(
    collection_name="menu_items",
    schema=schema,
    index_params=index_params,
)

# 嵌入模型: 将文本编码为向量 (与 langchain.py 使用同一模型, 保证检索一致)
embedding_model = init_embeddings(
    model=os.getenv("CLOSEAI_EMBEDDING_MODEL", ""),
    api_key=os.getenv("CLOSEAI_API_KEY", ""),
    base_url=os.getenv("CLOSEAI_BASE_URL", ""),
)

# 批量编码全部菜品文本为向量
vector_list = embedding_model.embed_documents(str_list)

# 构造插入数据: 每条记录包含 vector 与 text, id 由 auto_id 自动生成
insert_data = []
for item, vector in zip(str_list, vector_list):
    insert_data.append(
        {
            "vector": vector,
            "text": item,
        }
    )

insert_result = milvus_client.insert(
    collection_name="menu_items",
    data=insert_data,
)
rprint(insert_result)
