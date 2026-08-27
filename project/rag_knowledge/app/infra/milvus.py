from ..shared.clients.milvus_utils import get_milvus_client
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

    def client(self):
        """获取 Milvus 客户端"""
        return get_milvus_client()


infra_milvus = InfraMilvus()
# print(infra_milvus.chunks_collection)
# print(infra_milvus.item_name_collection)
# print(infra_milvus.client())
