import json
from pathlib import Path

from langchain.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from pymilvus import DataType

from ...infra.milvus import infra_milvus
from ...infra.model import infra_model
from ...process.load.agent.state import LoadState
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log
from .config import ITEM_NAME_CONTEXT_CHUNK_K, ITEM_NAME_CONTEXT_TOTAL_MAX_CHARS


@step_log("recognize_and_index_item_name")
def recognize_and_index_item_name(state: LoadState) -> LoadState:
    # 1.获取并校验参数
    chunks, file_title, md_path = _validate_date(state)

    # 2.调用LLM识别item_name
    item_name = _call_llm_return_item_name(chunks, file_title)

    # 3.填充item_name到chunks
    _padding_item_name_to_chunks(chunks, item_name)

    # 4.备份带有item_name的chunks到json文件
    _backup_chunks_json_with_item_name(md_path, chunks)

    # 5.创建item_name集合
    _prepared_item_name_collection()

    # 6.插入item_name向量数据到item_name集合
    _insert_item_name_data(item_name, file_title)

    state["item_name"] = item_name
    state["chunks"] = chunks
    return state


@step_log("_validate_date")
def _validate_date(state: LoadState) -> tuple[list[dict[str, str]], str, str]:
    md_path: str = state.get("md_path", "")
    chunks = state.get("chunks", [])
    file_title = state.get("file_title", "")

    if not md_path:
        logger.error("md_path 参数为空")
        raise ValueError("md_path 参数为空")

    md_path_obj: Path = Path(md_path)

    if not chunks:
        if md_path_obj.is_file():
            json_path_obj: Path = md_path_obj.with_name(f"{md_path_obj.stem}.json")
            if json_path_obj.is_file():
                chunks = json.loads(json_path_obj.read_text(encoding="utf-8"))
                state["chunks"] = chunks
            else:
                logger.error("chunks为空, 且json备份文件不存在")
                raise ValueError("chunks为空, 且json备份文件不存在")
        else:
            logger.error("chunks为空, 且Markdown文件不存在")
            raise ValueError("chunks为空, 且Markdown文件不存在")

    if not file_title:
        file_title = (md_path_obj.stem or "default").replace("_new", "")
        state["file_title"] = file_title
        logger.warning(f"file_title为空, 设置文件默认值: {file_title}")

    return chunks, file_title, md_path


@step_log("_call_llm_return_item_name")
def _call_llm_return_item_name(chunks: list[dict[str, str]], file_title: str) -> str:
    """调用LLM识别item_name"""
    model = infra_model.llm_model()

    # 1.拼接提示词中的辅助判断上下文 context
    context = ""
    for chunk in chunks[:ITEM_NAME_CONTEXT_CHUNK_K]:
        context += f"标题: {chunk.get('parent_title')}\n"
        context += f"内容: {chunk.get('content')}\n\n"
    context = context[:ITEM_NAME_CONTEXT_TOTAL_MAX_CHARS]

    # 2.加载拼接提示词
    item_name_recognition_prompt_text: str = load_prompt(
        name="item_name_recognition",
        file_title=file_title,
        context=context,
    )

    message = HumanMessage(content=item_name_recognition_prompt_text)
    chains = model | StrOutputParser()
    item_name = chains.invoke([message])

    if not item_name:
        item_name = file_title
        logger.warning(f"LLM 未识别出 item_name, 降级默认使用 file_title: {file_title}")

    return item_name


@step_log("_padding_item_name_to_chunks")
def _padding_item_name_to_chunks(chunks, item_name):
    """chunks 补充属性 item_name"""
    for chunk in chunks:
        chunk["item_name"] = item_name


def _backup_chunks_json_with_item_name(md_path: str, chunks: list[dict[str, str]]):
    """备份带有item_name的chunks到json文件"""
    json_path_obj: Path = Path(md_path).parent / f"{Path(md_path).stem}.json"
    json_path_obj.write_text(
        data=json.dumps(chunks, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    logger.info(f"chunks_with_item_name 数据备份完成, 备份位置:{json_path_obj!s}")


@step_log("_prepared_item_name_collection")
def _prepared_item_name_collection():
    """创建item_name对应的集合"""
    milvus_client = infra_milvus.client()

    # 检查集合是否已存在
    if milvus_client.has_collection(infra_milvus.item_name_collection):
        logger.info(f"集合已存在: {infra_milvus.item_name_collection}, 可直接使用")
        return
    logger.info(f"集合不存在: {infra_milvus.item_name_collection}, 开始创建")

    # 1.定义集合结构 schema
    schema = milvus_client.create_schema(
        auto_id=True,  # pk
        enable_dynamic_field=True,  # 支持动态字段
    )
    schema.add_field(field_name="pk", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="file_title", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="item_name", datatype=DataType.VARCHAR, max_length=512)
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
        collection_name=infra_milvus.item_name_collection,
        schema=schema,
        index_params=index_params,
    )
    logger.info(f"集合创建成功: {infra_milvus.item_name_collection}")


@step_log("_insert_item_name_data")
def _insert_item_name_data(item_name, file_title):
    """插入数据"""
    # 1.计算 item_name 向量
    result = infra_model.embedding([item_name])
    dense_vector = result["dense"][0]
    sparse_vector = result["sparse"][0]

    milvus_client = infra_milvus.client()

    # 2.先删除旧数据
    milvus_client.delete(
        collection_name=infra_milvus.item_name_collection,
        filter=f"file_title == '{file_title}'",  # 1.等于判断要用 == / 2.值要用引号
    )
    # 3.插入新数据
    milvus_client.insert(
        collection_name=infra_milvus.item_name_collection,
        data=[
            {
                "file_title": file_title,
                "item_name": item_name,
                "sparse_vector": sparse_vector,
                "dense_vector": dense_vector,
            }
        ],
    )
    logger.info(f"item_name 数据插入完成: {item_name}, file_title: {file_title}")
