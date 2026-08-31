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
        """获取 Milvus 客户端"""
        return get_milvus_client()

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
