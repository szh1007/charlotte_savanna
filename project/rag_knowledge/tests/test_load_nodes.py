"""加载链路 _05 / _06 / _07 三个节点的契约: chunk 数量与元数据字段.

C02 改过分块合并之后, 「进库的到底是几条, 每条带什么字段」成了最容易静默漂移
的一环: 节点自己不校验字段, 少一个 key 要等检索侧读不到才发现. 这里用假
Milvus 客户端 + 假本地模型把三个节点各自的契约钉住, 全程不碰真 Milvus /
真 bge-m3.

另附一条对 `embedding_utils.generate_embeddings` 的补充: `_06` 只是把稀疏向量
原样回填, 真正的 CSR → `{特征索引: 权重}` 转换发生在它下面一层.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.rag.load import embedding_service, index_service, item_name_service
from app.rag.load.config import EMBEDDING_BATCH_SIZE
from app.shared.model import embedding_utils


class _FakeMilvusClient:
    """记录 delete / insert 调用; has_collection 恒真 = 走「集合已存在」分支."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def has_collection(self, collection_name: str) -> bool:
        return True

    def delete(self, collection_name: str, filter: str) -> None:
        self.calls.append(
            ("delete", {"collection_name": collection_name, "filter": filter})
        )

    def insert(self, collection_name: str, data: list[dict]) -> None:
        self.calls.append(
            ("insert", {"collection_name": collection_name, "data": data})
        )


def _embedding_of(n: int) -> list[dict]:
    """造 n 条假 embeddings, 字段与切分 / 主体链路的产物一致."""
    return [
        {
            "file_title": "手册",
            "chunk_id": i,
            "content": f"内容{i}",
            "item_name": "主体",
            "title": f"## 节{i}",
            "parent_title": f"## 节{i}",
            "part": 1,
            "dense_vector": [0.1, 0.2],
            "sparse_vector": {i: 0.5},
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _07 index_service: 先按 file_title 删旧, 再整批插入
# ---------------------------------------------------------------------------


def test_index_node_inserts_every_embedding_with_its_metadata(monkeypatch):
    """进 insert 的条数与字段要和 embeddings 一一对应.

    少一条, 少一个字段都不会报错, 只会在检索侧变成「搜不到」—— 这正是
    C02 过后最需要盯住的形状.
    """
    client = _FakeMilvusClient()
    monkeypatch.setattr(index_service.infra_milvus, "require_client", lambda: client)
    embeddings = _embedding_of(7)

    index_service.index_chunks({"embeddings": embeddings})

    kinds = [kind for kind, _ in client.calls]
    assert kinds == ["delete", "insert"], "先按 file_title 删旧, 再插新"

    _, delete_payload = client.calls[0]
    assert (
        delete_payload["collection_name"]
        == index_service.infra_milvus.chunks_collection
    )
    assert delete_payload["filter"] == "file_title == '手册'"

    _, insert_payload = client.calls[1]
    assert (
        insert_payload["collection_name"]
        == index_service.infra_milvus.chunks_collection
    )
    inserted = insert_payload["data"]
    assert len(inserted) == len(embeddings), "一条 embeddings 都不能落下"

    expected_fields = {
        "chunk_id",
        "content",
        "file_title",
        "item_name",
        "title",
        "parent_title",
        "part",
        "dense_vector",
        "sparse_vector",
    }
    for row in inserted:
        assert expected_fields <= set(row)


# ---------------------------------------------------------------------------
# _06 embedding_service: 分批, 文本前缀, 向量回填
# ---------------------------------------------------------------------------


def _fake_embedding(calls: list[list[str]]):
    """把 embedding 挡掉, 返回可预测的假向量; 顺带记录每批喂进来的文本."""

    def fake(texts):
        calls.append(list(texts))
        return {
            "dense": [[float(len(text))] for text in texts],
            "sparse": [{len(text): 0.5} for text in texts],
        }

    return fake


def test_embedding_node_batches_by_config_and_prefixes_texts_with_item_name(
    monkeypatch,
):
    """每批不超过 EMBEDDING_BATCH_SIZE, 向量化文本是 item_name + "_" + content.

    前缀是检索时的主体锚 —— 只嵌 content 的话, 问「主体X的保养」时块里没有
    主体名可对齐; 批量大小错了则一次喂给模型的条数与配置不符.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        embedding_service.infra_model, "embedding", _fake_embedding(calls)
    )

    size = EMBEDDING_BATCH_SIZE
    total = size * 2 + 2
    chunks = [{"item_name": "主体", "content": f"内容{i}"} for i in range(total)]

    embedding_service.generate_chunk_embeddings({"chunks": chunks})

    assert [len(batch) for batch in calls] == [size, size, 2], "分批大小 = 配置值"
    flat = [text for batch in calls for text in batch]
    assert flat == [f"主体_内容{i}" for i in range(total)]


def test_embedding_node_backfills_every_chunk_with_both_vectors(monkeypatch):
    """向量回填一条不落, 且 sparse 保持 {特征索引: 权重} 的字典形态.

    回填落空的那条 chunk 进库时就没有向量, 检索永远召回不到它.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        embedding_service.infra_model, "embedding", _fake_embedding(calls)
    )
    chunks = [{"item_name": "主体", "content": f"内容{i}"} for i in range(3)]

    state = embedding_service.generate_chunk_embeddings({"chunks": chunks})

    assert len(state["embeddings"]) == len(chunks) == 3
    for chunk in chunks:
        expected_key = len(f"主体_{chunk['content']}")
        assert chunk["sparse_vector"] == {expected_key: 0.5}
        assert chunk["dense_vector"] == [float(expected_key)]


class _FakeArray:
    """够 `generate_embeddings` 用的最小数组替身: 支持切片 + tolist()."""

    def __init__(self, values):
        self._values = list(values)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return _FakeArray(self._values[item])
        return self._values[item]

    def tolist(self) -> list:
        return list(self._values)


class _FakeBgeM3:
    """假 BGE-M3: encode_documents 返回 CSR 形态的 sparse 与逐条的 dense."""

    def __init__(self, dense: list[list[float]], indptr, indices, data):
        self._dense = dense
        self._indptr = indptr
        self._indices = indices
        self._data = data

    def encode_documents(self, texts: list[str]) -> dict:
        return {
            "dense": [_FakeArray(vec) for vec in self._dense],
            "sparse": SimpleNamespace(
                indptr=self._indptr,
                indices=_FakeArray(self._indices),
                data=_FakeArray(self._data),
            ),
        }


def test_sparse_csr_rows_become_index_weight_dicts(monkeypatch):
    """模型吐出的 CSR 稀疏矩阵要拆成「每行一个 {特征索引: 权重} 字典」.

    Milvus 与 chunks json 备份都按这个形态消费; 留下 numpy 标量或整块 CSR
    都会坏在序列化上. 这是 `_06` 回填的那个 sparse_vector 的真正来源.
    """
    fake = _FakeBgeM3(
        dense=[[0.1, 0.2], [0.3, 0.4]],
        indptr=[0, 2, 4],
        indices=[3, 8, 20, 1],
        data=[0.7, 0.2, 0.1, 0.6],
    )
    monkeypatch.setattr(embedding_utils, "get_bge_m3_ef", lambda: fake)

    result = embedding_utils.generate_embeddings(["甲", "乙"])

    assert result["sparse"] == [{3: 0.7, 8: 0.2}, {20: 0.1, 1: 0.6}]
    assert result["dense"] == [[0.1, 0.2], [0.3, 0.4]]


# ---------------------------------------------------------------------------
# _05 item_name_service: 主体名写进每个 chunk + LLM 空答降级
# ---------------------------------------------------------------------------


class _FakeLlmModel:
    """假 LLM: 只支持 `|`(接输出解析器) 与链上的 invoke —— 不加载真模型."""

    def __init__(self, output):
        self._output = output

    def __or__(self, other):
        return _FakeLlmChain(self._output)


class _FakeLlmChain:
    def __init__(self, output):
        self._output = output

    def invoke(self, messages):
        return self._output


def _patch_recognize_seams(monkeypatch, llm_output: str) -> _FakeMilvusClient:
    """把 LLM / embedding / Milvus 都换成假的; json 备份由调用方给的 tmp_path 承接."""
    client = _FakeMilvusClient()
    monkeypatch.setattr(
        item_name_service.infra_milvus, "require_client", lambda: client
    )
    monkeypatch.setattr(
        item_name_service.infra_model,
        "llm_model",
        lambda *args, **kwargs: _FakeLlmModel(llm_output),
    )
    monkeypatch.setattr(
        item_name_service.infra_model,
        "embedding",
        lambda texts: {"dense": [[0.1]], "sparse": [{1: 0.5}]},
    )
    return client


def test_recognize_node_stamps_the_llm_result_onto_every_chunk(monkeypatch, tmp_path):
    """识别出的主体要写进每个 chunk 与备份 json, 并单独入库 item_name 向量.

    这三个出口都是后续检索的凭据: chunk 里缺 item_name, 记录就永远不会被
    主体过滤命中; item_name 集合缺记录, 用户提问就确认不出主体.
    """
    client = _patch_recognize_seams(monkeypatch, "某设备X")
    chunks = [{"content": f"内容{i}", "parent_title": f"## 节{i}"} for i in range(3)]
    state = {
        "md_path": str(tmp_path / "doc.md"),
        "file_title": "手册",
        "chunks": chunks,
    }

    result = item_name_service.recognize_and_index_item_name(state)

    assert all(chunk["item_name"] == "某设备X" for chunk in chunks)
    assert result["item_name"] == "某设备X"

    backup = json.loads((tmp_path / "doc.json").read_text(encoding="utf-8"))
    assert [row["item_name"] for row in backup] == ["某设备X"] * 3

    kinds = [kind for kind, _ in client.calls]
    assert kinds == ["delete", "insert"], "先按 file_title 删旧, 再插新"
    inserted = client.calls[1][1]["data"]
    assert len(inserted) == 1
    assert inserted[0]["item_name"] == "某设备X"
    assert inserted[0]["file_title"] == "手册"
    assert inserted[0]["dense_vector"] == [0.1]
    assert inserted[0]["sparse_vector"] == {1: 0.5}


def test_empty_llm_answer_degrades_to_the_file_title(monkeypatch, tmp_path):
    """LLM 没识别出主体 (空答) 时降级用 file_title.

    文档名至少是个可用主体; 空串写进库则所有块都过滤不到, 等于这份文档
    从主体链路上消失.
    """
    _patch_recognize_seams(monkeypatch, "")
    chunks = [{"content": "内容0", "parent_title": "## 节0"}]
    state = {
        "md_path": str(tmp_path / "doc.md"),
        "file_title": "手册",
        "chunks": chunks,
    }

    result = item_name_service.recognize_and_index_item_name(state)

    assert result["item_name"] == "手册"
    assert chunks[0]["item_name"] == "手册"
