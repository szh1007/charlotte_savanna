"""Embedding 接入 (L5-a) - 默认 bge-m3, 可注册其它实现.

搬运自 `project/charplot/rag/embeddings.py`, 改动只有一处: 配置从「charplot 的
模块级常量」换成**传进来的 `KnowledgeConfig`** (本项目的规矩: 读 env 只在
`CharApp/minimall/config.py` 一处, 其余模块拿一份只读配置).

`Embedder` 协议是索引与检索两侧共用的最小接口: 批量文档向量与单条查询向量,
都返回 `{"dense": [...], "sparse": ...}` 的同构结构 (bge-m3 一次编码同时出稠密与
稀疏两路, 混合检索的两条腿就靠它).

**只认本地模型, 拒绝静默下载** (charplot 那条纪律原样保留): bge-m3 约 2GB, 而
「跑一次索引 / 起一次服务」不该在背后下载几百 MB. 模型缺失时 `get_embedder` 会
在第一次编码时抛错, 错误信息里带着可照抄的 modelscope 下载命令.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from CharApp.minimall.config import KnowledgeConfig, MinimallConfigError

logger = logging.getLogger(__name__)

# 模型注册表: 名称 → 构建函数 (配置可切换 + 测试注入点)
_embedder_factories: dict[str, Callable[[KnowledgeConfig], Embedder]] = {}

# 惰性单例: 模型加载一次就够 (bge-m3 在内存里约 4GB, 每次检索新建一份是灾难)
_embedder_instance: Embedder | None = None


class Embedder(Protocol):
    """Embedding 抽象: 实现此协议即可接入索引/检索链路 (可切换).

    **可选**: 实现一个无参 `preload()` 表示"能把模型提前读进内存" —— 启动预热
    (`knowledge/prewarm.py`) 会调它 (没有这个方法就跳过预热, 不影响加载与检索).
    它不进协议本体是因为它是**优化**不是能力: 一个只实现两个 embed_* 的实现照样
    能跑完整条链路, 只是第一次编码慢一点.
    """

    def embed_documents(self, texts: list[str]) -> dict:
        """批量文档向量: 返回 {"dense": [...], "sparse": [...]}, 与输入一一对应."""
        ...

    def embed_query(self, text: str) -> dict:
        """单条查询向量: 返回 {"dense": [...], "sparse": {...}}."""
        ...


def register_embedder(
    name: str, factory: Callable[[KnowledgeConfig], Embedder]
) -> None:
    """注册一个实现 (get_embedder 按 `config.embedding_model` 构建)."""
    _embedder_factories[name] = factory


def get_embedder(config: KnowledgeConfig) -> Embedder:
    """按配置构建 Embedder 单例 (默认 bge-m3, 模型**惰性加载**).

    第一次调用只建对象, 真正的模型加载发生在第一次编码 (或 `prewarm` 显式预热)
    时 —— 于是「配置读进来」与「4GB 进内存」是两件事, 后者看得见也推迟得起.

    Raises:
        MinimallConfigError: `config.embedding_model` 不在注册表里 (名字写错了).
            与其它配置错误同一个类型: 两个入口都把它翻成一行人话.
    """
    global _embedder_instance
    if _embedder_instance is None:
        factory = _embedder_factories.get(config.embedding_model)
        if factory is None:
            raise MinimallConfigError(
                f"未注册的 embedding 模型: {config.embedding_model!r} "
                f"(已注册: {sorted(_embedder_factories)})"
            )
        _embedder_instance = factory(config)
        logger.info("embedding 实现已选定: %s", config.embedding_model)
    return _embedder_instance


def reset_embedder() -> None:
    """把单例丢掉 —— **只给用例用** (不同配置 / 不同替身之间要互不串味).

    生产不调它: 一个进程一份模型正是想要的; 这条存在的理由与 `db/testing.py`
    那些支撑一样, 是测试接缝而不是产品功能.
    """
    global _embedder_instance
    _embedder_instance = None


def _csr_to_dict(csr, row: int) -> dict:
    """CSR 稀疏矩阵第 row 行 → {特征索引: 权重} (与 charplot 同款拆解)."""
    start, end = csr.indptr[row], csr.indptr[row + 1]
    indices = csr.indices[start:end].tolist()
    data = csr.data[start:end].tolist()
    return dict(zip(indices, data))


class BgeM3Embedder:
    """BGE-M3 本地模型实现 (pymilvus 内置, 稠密 + 稀疏一次编码).

    模型原生 `normalize_embeddings=True` (稠密/稀疏都 L2 归一化, 与 Milvus 的
    IP 内积检索匹配); 加载前经 `config.resolve_local_model_path` 校验本地目录
    存在 —— 缺失**直接报错**, 阻止 pymilvus 静默从 HuggingFace 重新下载
    (bge-m3 约 2GB, 下载是主动行为).
    """

    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        self._config = config
        self._model_name = model_name or config.embedding_model_name
        self._device = device or config.embedding_device
        self._model = None  # 惰性加载 (首次编码时)

    def _download_hint(self) -> str:
        """缺失时那句可照抄的下载命令 (`org/name` 引用才有得给).

        引用是一个本地路径时给不出"该从哪儿下" —— 那种情况只报期望路径.
        期望路径由 `KnowledgeConfig.local_model_dir` 算 (**平铺布局的规则只有那一处**,
        不在这里再拼一遍: 布局改了要让两个地方一起改的话, 迟早只改一处).
        """
        target = self._config.local_model_dir(self._model_name)
        if target == Path(self._model_name):  # 引用本身就是个路径, 没有"从哪下"可给
            return ""
        return (
            f", 可执行: modelscope download --model {self._model_name} "
            f"--local_dir {target}"
        )

    def _get_model(self):
        if self._model is None:
            from pymilvus.model.hybrid import BGEM3EmbeddingFunction

            model_path = self._config.resolve_local_model_path(self._model_name)
            if model_path is None:
                raise RuntimeError(
                    f"embedding 模型本地不存在, 拒绝加载: {self._model_name}. "
                    f"它在 modelscope 根目录 {self._config.modelscope_root} 下"
                    f"解析不到 (目录里要有 config.json)"
                    f"{self._download_hint()}. 本链路只认本地模型, "
                    f"不会自动从 HuggingFace 下载"
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
                use_fp16=self._config.embedding_fp16,
                normalize_embeddings=True,
            )
        return self._model

    def preload(self) -> None:
        """把模型加载进内存 (不编码) —— `prewarm` 调它, 见 knowledge/prewarm.py."""
        self._get_model()

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


__all__ = [
    "BgeM3Embedder",
    "Embedder",
    "get_embedder",
    "register_embedder",
    "reset_embedder",
]
