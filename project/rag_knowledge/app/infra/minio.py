from ..shared.clients.minio_utils import get_minio_client
from .config import infra_config


class InfraMinIO:
    @property
    def bucket_name(self):
        """存储桶名称"""
        return infra_config.minio_config.bucket_name

    @property
    def image_dir(self):
        """存储图片的公共前缀"""
        return infra_config.minio_config.minio_img_dir

    @property
    def client(self):
        """客户端"""
        return get_minio_client()

    def build_image_url(self, dir_name: str, file_name: str):
        http = "https" if infra_config.minio_config.minio_secure else "http"
        endpoint = infra_config.minio_config.endpoint
        image_url = f"{http}://{endpoint}/{self.bucket_name}/{self.image_dir}/{dir_name}/{file_name}"
        return image_url


infra_minio = InfraMinIO()
print(infra_minio.build_image_url("test", "test.jpg"))
