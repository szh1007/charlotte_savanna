"""
MinerU 配置模块, 负责读取文档解析服务相关环境变量.
"""

from dataclasses import dataclass

from .common import env_int, env_str


@dataclass
class MinerUConfig:
    base_url: str
    api_key: str
    model_vision: str
    poll_timeout_seconds: int
    poll_interval_seconds: int
    download_timeout_seconds: int


mineru_config = MinerUConfig(
    base_url=env_str("RK_MINERU_BASE_URL"),
    api_key=env_str("RK_MINERU_API_TOKEN"),
    # 模型版本配置(vlm = 视觉语言模型, PDF/图片高精度解析)
    model_vision=env_str("RK_MINERU_MODEL_VISION"),
    # 任务轮询最大超时时间, 超过则判定任务失败(一个pdf约等于1秒)
    poll_timeout_seconds=env_int("RK_MINERU_POLL_TIMEOUT_SECONDS"),
    # 任务轮询间隔时间, 每隔多久查询一次任务状态
    poll_interval_seconds=env_int("RK_MINERU_POLL_INTERVAL_SECONDS"),
    # 文件下载超时时间, 下载文件超过此时长则中断
    download_timeout_seconds=env_int("RK_MINERU_DOWNLOAD_TIMEOUT_SECONDS"),
)
