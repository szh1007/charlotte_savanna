"""
工具模块, 负责提供 minio 相关的辅助能力.
"""

import json

# 导入 MinIO 官方 Python SDK 核心类(用于 MinIO 对象存储的客户端操作)
from minio import Minio

# 导入项目内部配置与日志工具
from app.shared.config.minio_config import (
    minio_config,
)  # MinIO 相关配置(端点, 密钥, 桶名等)
from app.shared.runtime.logger import logger  # 项目统一日志工具

# 全局 MinIO 客户端实例(单例模式, 避免重复创建连接, 提升性能)
_minio_client: Minio | None = None


# 1. 定义 MinIO 客户端连接创建函数(私有函数, 仅内部调用)
def _create_minio_client() -> Minio:
    """
    创建并返回 MinIO 客户端连接
    核心作用: 读取配置文件中的 MinIO 参数, 初始化客户端连接

    :return: 初始化完成的 MinIO 客户端对象
    """
    return Minio(
        endpoint=minio_config.endpoint,  # MinIO 服务端点(IP: 端口)
        access_key=minio_config.access_key,  # MinIO 访问密钥
        secret_key=minio_config.secret_key,  # MinIO 秘密密钥
        secure=minio_config.minio_secure,  # 是否启用 HTTPS(True/False)
    )


# 2. 定义桶访问策略生成函数(私有函数, 仅内部调用)
def _set_bucket_policy(bucket_name: str) -> str:
    """
    生成 MinIO 桶的访问策略字符串(JSON 格式)
    核心策略: 允许所有用户(Principal: "*")对桶内所有对象执行读取操作(s3:GetObject)
    适配场景: 图片上传后需公开访问(如 MD 中图片在线 URL)

    :param bucket_name: 目标桶名
    :return: 序列化后的 JSON 格式访问策略字符串
    """
    # 策略模板(遵循 AWS S3 策略规范, MinIO 兼容该规范)
    policy = {
        "Version": "2012-10-17",  # 策略版本(固定值, 兼容 S3 标准)
        "Statement": [
            {
                "Effect": "Allow",  # 策略效果: 允许访问
                "Principal": {"AWS": ["*"]},  # 授权对象: 所有用户
                "Action": ["s3:GetObject"],  # 授权操作: 读取桶内对象
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],  # 授权范围: 桶内所有对象
            }
        ],
    }
    # 将字典策略序列化为 JSON 字符串, 供 MinIO 设置使用
    return json.dumps(policy)


# 3. 定义桶初始化函数(私有函数, 仅内部调用)
def _create_bucket_ready(client: Minio):
    """
    检查 MinIO 桶是否存在, 不存在则创建, 并设置访问策略
    核心作用: 确保图片上传所需的桶已就绪, 避免上传失败

    :param client: 已初始化的 MinIO 客户端对象
    """
    bucket_name = minio_config.bucket_name  # 从配置中获取目标桶名
    # 检查桶是否存在
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)  # 不存在则创建桶
        # 为新桶设置访问策略(允许公开读取, 适配图片在线访问需求)
        client.set_bucket_policy(bucket_name, _set_bucket_policy(bucket_name))
        logger.info(f"MinIO 桶 {bucket_name} 已创建, 并设置访问策略")
    else:
        # 桶已存在, 仅打印日志, 不重复操作
        logger.info(f"MinIO 桶 {bucket_name} 已存在, 无需重复创建")


def get_minio_client() -> Minio:
    """
    获取全局 MinIO 客户端(懒加载模式, 无锁版本, 适配单线程场景)
    核心逻辑:
    1. 首次调用: 初始化客户端 + 检查/创建桶 + 设置策略, 将客户端实例赋值给全局变量
    2. 后续调用: 直接复用全局客户端实例, 避免重复创建连接(提升性能, 节省资源)

    :return: 全局唯一的 MinIO 客户端对象
    """
    # 声明使用全局变量(修改全局变量需显式声明)
    global _minio_client

    # 懒加载: 仅在客户端未初始化时执行创建逻辑
    if _minio_client is None:
        logger.info("开始初始化 MinIO 客户端(首次调用, 执行懒加载)")
        client = _create_minio_client()  # 创建客户端连接
        _create_bucket_ready(client)  # 检查并初始化桶
        _minio_client = client  # 赋值给全局变量, 供后续复用
        logger.info("MinIO 客户端初始化完成, 已就绪可使用")

    # 复用全局客户端实例, 直接返回
    return _minio_client
