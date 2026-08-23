"""
工具模块, 负责提供 milvus 相关的辅助能力.
"""

from pymilvus import AnnSearchRequest, MilvusClient, WeightedRanker

from ..config.milvus_config import milvus_config
from ..runtime.logger import logger

# 全局 Milvus 客户端实例, 实现单例复用
_milvus_client: MilvusClient | None = None


def get_milvus_client() -> MilvusClient | None:
    """
    Milvus 客户端单例获取方法
    实现客户端连接复用, 避免重复创建连接消耗资源

    :return: MilvusClient 实例, 连接失败返回 None
    """
    try:
        global _milvus_client
        # 单例判断: 未初始化则创建新连接
        if _milvus_client is None:
            milvus_uri = milvus_config.milvus_url
            # 校验 Milvus 连接地址配置
            if not milvus_uri:
                logger.error("Milvus 客户端连接失败: 缺少 MILVUS_URL 环境变量配置")
                return None
            # 初始化 Milvus 客户端
            _milvus_client = MilvusClient(milvus_uri)
            logger.info("Milvus 客户端连接成功")
        return _milvus_client
    except Exception as e:
        logger.error(f"Milvus 客户端连接异常: {e!s}", exc_info=True)
        return None


def create_hybrid_search_requests(
    dense_vector,
    sparse_vector,
    dense_params=None,
    sparse_params=None,
    expr=None,
    limit=5,
):
    """
    构建 Milvus 混合搜索请求对象
    分别创建稠密/稀疏向量的搜索请求, 用于后续混合搜索融合

    :param dense_vector: 文本生成的稠密向量
    :param sparse_vector: 文本生成的稀疏向量
    :param dense_params: 稠密向量搜索参数, 默认使用余弦相似度
    :param sparse_params: 稀疏向量搜索参数, 默认使用内积相似度
    :param expr: 搜索过滤表达式, 用于精准筛选数据
    :param limit: 单向量搜索返回结果数量, 默认5
    :return: 搜索请求列表, 包含 [dense_req, sparse_req]
    """
    # 稠密向量默认搜索参数: 内积(IP), BGE-M3 已做 L2 归一化, IP 等价于余弦相似度
    if dense_params is None:
        dense_params = {"metric_type": "IP"}
    # 稀疏向量默认搜索参数: 内积(IP), 适配 BGE-M3 稀疏向量
    if sparse_params is None:
        sparse_params = {"metric_type": "IP"}

    # 构建稠密向量搜索请求, 关联 Milvus 的 dense_vector 字段
    # 近似最近邻(ANN)检索请求的核心类
    dense_req = AnnSearchRequest(
        data=[dense_vector],
        anns_field="dense_vector",
        param=dense_params,
        expr=expr,
        limit=limit,
    )

    # 构建稀疏向量搜索请求, 关联 Milvus 的 sparse_vector 字段
    sparse_req = AnnSearchRequest(
        data=[sparse_vector],
        anns_field="sparse_vector",
        param=sparse_params,
        expr=expr,
        limit=limit,
    )

    return [dense_req, sparse_req]


def hybrid_search(
    client,
    collection_name,
    reqs,
    ranker_weights=(0.5, 0.5),
    norm_score=False,
    limit=5,
    output_fields=None,
    search_params=None,
):
    """
    执行 Milvus 稠密 + 稀疏向量混合搜索
    基于 WeightedRanker 实现双向量搜索结果加权融合, 提升检索准确性

    :param client: MilvusClient 实例
    :param collection_name: 集合名称
    :param reqs: 搜索请求列表, 固定为 [dense_vector, sparse_vector]
    :param ranker_weights: 加权融合权重, 默认 (0.5, 0.5), 依次对应稠密向量和稀疏向量
    :param norm_score: 是否归一化评分后再融合, 避免评分量级差异导致权重失效
    :param limit: 混合搜索最终返回结果数量, 默认 5
    :param output_fields: 需要返回的字段列表, 默认返回 item_name
    :param search_params: 搜索参数, 如 ef/topk 等, 默认 None
    :return: 混合搜索结果列表, 搜索失败返回 None
    """
    try:
        # 初始化加权排名器: 按照权重融合稠密和稀疏向量的搜索结果
        # norm_score=True: 优先将两个向量评分归一化到 0~1 区间, 再加权计算
        rerank = WeightedRanker(
            ranker_weights[0], ranker_weights[1], norm_score=norm_score
        )

        # 默认返回字段: 文档标识字段
        if output_fields is None:
            output_fields = ["item_name"]

        # 执行混合搜索: 融合稠密 + 稀疏向量结果, 按权重重新排序
        res = client.hybrid_search(
            collection_name=collection_name,
            reqs=reqs,
            ranker=rerank,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params,
        )

        result_count = len(res[0]) if res and res[0] else 0
        logger.info(
            f"Milvus 混合搜索完成, 集合[{collection_name}]共检索到{result_count}条结果"
        )
        return res
    except Exception as e:
        logger.error(
            f"Milvus 混合搜索执行失败, 集合[{collection_name}]:{e!s}", exc_info=True
        )
        return None
