"""
工具脚本, 用于处理 download reranker 相关的辅助任务.
"""

from pathlib import Path

from modelscope.hub.snapshot_download import snapshot_download

model_id = "BAAI/bge-reranker-large"
local_cache_dir = Path("D:/__WorkSpace__/.modelscope_cache/models/rerank")

local_cache_dir.mkdir(parents=True, exist_ok=True)

model_dir = snapshot_download(model_id=model_id, cache_dir=str(local_cache_dir))

print(f"{model_id} 模型已下载到: {model_dir}")
