"""Embedding 模型接入抽象 (Issue 10, SPEC §7.2) - 可切换.

Embedder 协议定义索引与检索两侧的最小接口 (embed_documents / embed_query),
返回 {dense: [[float,...], ...], sparse: [{idx: weight}, ...]} 同构结构,
稠密向量 L2 归一化 (适配 Milvus IP 内积检索, 参考 rag_knowledge 实现).

默认实现 BgeM3Embedder (pymilvus BGEM3EmbeddingFunction, 本地模型一次出
稠密+稀疏); 新增模型实现协议并在 get_embedder 工厂注册 (配置切换, 测试
注入 FakeEmbedder 走同一入口).
"""

import logging
from typing import Protocol

from ..api import config

logger = logging.getLogger(__name__)

# 模型注册表: 名称 → 构建函数 (配置可切换 + 测试注入点)
_embedder_factories: dict[str, callable] = {}


class Embedder(Protocol):
    """Embedding 抽象: 实现此协议即可接入索引/检索链路 (可切换)."""

    def embed_documents(self, texts: list[str]) -> dict:
        """批量文档向量: 返回 {"dense": [...], "sparse": [...]} 与输入一一对应."""
        ...

    def embed_query(self, text: str) -> dict:
        """单条查询向量: 返回 {"dense": [...], "sparse": {...}}."""
        ...


def register_embedder(name: str, factory: callable) -> None:
    """注册模型实现 (get_embedder 按名称构建)."""
    _embedder_factories[name] = factory


def get_embedder(model_name: str | None = None) -> Embedder:
    """按配置构建 Embedder 单例 (默认 bge-m3, 惰性加载模型).

    model_name 覆盖 config.EMBEDDING_MODEL (测试注入 / 模型切换调试用).
    """
    name = model_name or config.EMBEDDING_MODEL
    factory = _embedder_factories.get(name)
    if factory is None:
        raise RuntimeError(
            f"未注册的 embedding 模型: {name} (已注册: {sorted(_embedder_factories)})"
        )
    return factory()


def _csr_to_dict(csr, row: int) -> dict:
    """CSR 稀疏矩阵第 row 行 → {特征索引: 权重} (参考 rag_knowledge 拆解)."""
    start, end = csr.indptr[row], csr.indptr[row + 1]
    indices = csr.indices[start:end].tolist()
    data = csr.data[start:end].tolist()
    return dict(zip(indices, data))


class BgeM3Embedder:
    """BGE-M3 本地模型实现 (pymilvus 内置, 稠密+稀疏一次编码).

    模型原生 normalize_embeddings=True (稠密/稀疏均 L2 归一化, 与 Milvus
    IP 检索匹配); 模型加载首次调用触发, 仅接受本地已存在目录 (modelscope
    下载, resolve_local_model_path 校验, 缺失报错不自动下载; 测试用
    FakeEmbedder 隔离, 不加载真实模型).
    """

    def __init__(self, model_name: str | None = None, device: str | None = None):
        self._model_name = model_name or config.EMBEDDING_MODEL_NAME
        self._device = device or config.EMBEDDING_DEVICE
        self._model = None  # 惰性加载 (首次 encode 时)

    def _get_model(self):
        if self._model is None:
            from pymilvus.model.hybrid import BGEM3EmbeddingFunction

            # 仅加载本地已存在的模型 (config.resolve_local_model_path), 本地缺失
            # 直接报错 — 阻止 pymilvus 静默从 HF 重新下载 (~2GB), 下载是主动行为
            model_path = config.resolve_local_model_path(self._model_name)
            if model_path is None:
                raise RuntimeError(
                    f"embedding 模型本地不存在, 拒绝加载: {self._model_name}. "
                    f"期望位于 modelscope 目录 "
                    f"{config.MODELSCOPE_ROOT}/models/BAAI/bge-m3 (含 config.json), "
                    f"可执行: modelscope download --model BAAI/bge-m3 "
                    f"--local_dir {config.MODELSCOPE_ROOT}/models/BAAI/bge-m3"
                )
            if model_path != self._model_name:
                logger.info(
                    "embedding 模型引用 %s → 本地 %s", self._model_name, model_path
                )
            logger.info(
                "初始化 BGE-M3 embedding 模型 (model=%s, device=%s)",
                model_path,
                self._device,
            )
            self._model = BGEM3EmbeddingFunction(
                model_name=model_path,
                device=self._device,
                use_fp16=config.EMBEDDING_FP16,
                normalize_embeddings=True,
            )
        return self._model

    def _encode(self, texts: list[str]) -> dict:
        out = self._get_model().encode_documents(texts)
        sparse = [_csr_to_dict(out["sparse"], i) for i in range(len(texts))]
        return {
            "dense": [emb.tolist() for emb in out["dense"]],
            "sparse": sparse,
        }

    def embed_documents(self, texts: list[str]) -> dict:
        if not texts:
            raise ValueError("embed_documents: texts 不能为空")
        return self._encode(texts)

    def embed_query(self, text: str) -> dict:
        out = self._encode([text])
        return {"dense": out["dense"][0], "sparse": out["sparse"][0]}


register_embedder("bge-m3", BgeM3Embedder)
