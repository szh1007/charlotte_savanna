from pymilvus import DataType

from ...infra.milvus import infra_milvus
from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import logger, step_log


@step_log("index_chunks")
def index_chunks(state: LoadState) -> LoadState:
    # 1.获取并校验参数
    embeddings_content = _validate_data(state)

    # 2.创建chunks集合
    _prepared_chunks_collection()

    # 3.插入chunks向量数据到chunks集合
    _insert_chunks_data(embeddings_content)

    return state


@step_log("_validate_data")
def _validate_data(state: LoadState) -> list[dict[str, str]]:
    """获取并校验参数"""
    embeddings_content = state.get("embeddings_content")
    if not embeddings_content:
        logger.error("embeddings_content 为空")
        raise ValueError("embeddings_content 为空")
    return embeddings_content


@step_log("_prepared_chunks_collection")
def _prepared_chunks_collection():
    """创建chunks集合"""
    milvus_client = infra_milvus.client()

    # 检查集合是否已存在
    if milvus_client.has_collection(infra_milvus.chunks_collection):
        logger.info(f"集合已存在: {infra_milvus.chunks_collection}, 可直接使用")
        return
    logger.info(f"集合不存在: {infra_milvus.chunks_collection}, 开始创建")

    # 1.定义集合结构 schema
    schema = milvus_client.create_schema(
        auto_id=True,  # pk
        enable_dynamic_field=True,  # 支持动态字段
    )
    schema.add_field(field_name="chunk_id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)
    schema.add_field(field_name="file_title", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="item_name", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(
        field_name="parent_title", datatype=DataType.VARCHAR, max_length=512
    )
    schema.add_field(field_name="part", datatype=DataType.INT8)
    schema.add_field(
        field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=infra_milvus.dim
    )
    schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)

    # 2.创建集合索引 index
    index_params = milvus_client.prepare_index_params()
    # 2.1 稠密向量索引
    # FLAT: 全量搜索
    # IVF_FLAT: K均值聚类, 效率高, 精度较低
    # HNSW: 分层图导航, 效率较低, 精准高
    index_params.add_index(
        field_name="dense_vector",
        index_type="HNSW",
        index_name="dense_vector_index",
        params={
            "M": 64,  # 相邻最大的节点数量
            "efConstruction": 100,  # 候选的节点数量
        },
        metric_type="COSINE",  # 稠密推荐 COSINE / IP / L2
    )
    # 2.2 稀疏向量索引
    index_params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",  # 倒排索引
        index_name="sparse_vector_index",
        params={"inverted_index_algo": "DAAT_MAXSCORE"},
        metric_type="IP",  # 稀疏推荐 IP (同时考虑【关键词命中】和【向量的相似度】)
    )

    # 创建集合 collection
    milvus_client.create_collection(
        collection_name=infra_milvus.chunks_collection,
        schema=schema,
        index_params=index_params,
    )
    logger.info(f"集合创建成功: {infra_milvus.chunks_collection}")


@step_log("_insert_chunks_data")
def _insert_chunks_data(embeddings_content: list[dict]):
    """插入数据"""
    file_title: str = embeddings_content[0].get("file_title")
    milvus_client = infra_milvus.client()

    # 2.先删除旧数据
    milvus_client.delete(
        collection_name=infra_milvus.chunks_collection,
        filter=f"file_title == '{file_title}'",  # 1.等于判断要用 == / 2.值要用引号
    )
    # 3. 插入数据
    milvus_client.insert(
        collection_name=infra_milvus.chunks_collection,
        data=embeddings_content,  # 批量插入
    )
    logger.info(f"{file_title}文档chunks导入向量数据库完成: {len(embeddings_content)}")
