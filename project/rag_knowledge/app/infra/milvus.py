from typing import Any

from ..shared.clients.milvus_utils import (
    create_hybrid_search_requests,
    get_milvus_client,
    hybrid_search,
)
from .config import infra_config


class InfraMilvus:
    @property
    def chunks_collection(self) -> str:
        """获取 chunks 集合名称"""
        return infra_config.milvus_config.chunks_collection

    @property
    def item_name_collection(self) -> str:
        """获取 item_name 集合名称"""
        return infra_config.milvus_config.item_name_collection

    @property
    def dim(self) -> int:
        """获取 item_name 集合维度"""
        return infra_config.milvus_config.dim

    def client(self):
        """获取 Milvus 客户端 (连不上返回 None, 供健康检查判空用)"""
        return get_milvus_client()

    def require_client(self):
        """取客户端, **取不到就报错**.

        载入链路用这个: `client()` 返回 None 是给健康检查判空用的
        (`rag_eval/runner.py` 就是 `is not None`), 而 load 侧四个调用点此前拿到
        None 直接 `.has_collection(...)`, 崩在 AttributeError 上 —— 真正的错因
        (Milvus 连不上 / 没配 URL) 反被这一行不相干的类型错误盖住 (issue C02).
        """
        client = self.client()
        if client is None:
            raise RuntimeError(
                "Milvus 客户端不可用 (连接失败或未配置 MILVUS_URL), 无法执行索引操作"
            )
        return client

    def create_requests(
        self,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        *,
        expr: str | None = None,
        limit: int = 5,
    ):
        return create_hybrid_search_requests(
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            expr=expr,
            limit=limit,
        )

    def hybrid_search(
        self,
        *,
        collection_name: str,
        reqs: list[Any],
        ranker_weights: tuple[float, float] = (
            0.5,
            0.5,
        ),  # 权重
        norm_score: bool = False,
        limit: int = 5,
        output_fields: list[str] | None = None,
        search_params: dict | None = None,
    ):
        return hybrid_search(
            client=self.client(),
            collection_name=collection_name,
            reqs=reqs,
            ranker_weights=ranker_weights,
            norm_score=norm_score,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params,
        )


infra_milvus = InfraMilvus()
# print(infra_milvus.chunks_collection)
# print(infra_milvus.item_name_collection)
# print(infra_milvus.client())
