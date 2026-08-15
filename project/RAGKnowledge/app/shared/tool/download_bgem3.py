"""
工具脚本, 用于处理 download bgem3 相关的辅助任务.
"""

from modelscope.hub.snapshot_download import snapshot_download

model_dir = snapshot_download("BAAI/bge-m3", cache_dir="D:/modelscope_cache/models")
print(f"模型已下载到: {model_dir}")
