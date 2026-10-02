"""
RAG 评估执行模块.

可以把这个文件理解成"评估流程主脚本", 主要负责 3 件事:

1. 读取题库, 逐条走真实查询链路;
2. 统计每一层的召回效果;
3. 输出汇总结果和报告文件.

**评测知识从哪来**: 由真实加载链路 (`load_graph`) 建进 Milvus, 这个包不自己导入
测试数据 —— 见 `dataset.py` 的说明.

这个文件不追求过度抽象, 优先保证:
- 顺着往下读就能看懂;
- 关键参数和返回值写清楚;
- 每一步为什么做能说清楚.
"""

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from unittest.mock import patch

from app.infra.milvus import infra_milvus
from app.process.query.agent.state import create_query_default_state
from app.process.query.nodes._08_item_name_confirm import node_item_name_confirm
from app.process.query.nodes._09_1_search_embedding import node_search_embedding
from app.process.query.nodes._09_2_search_embedding_hyde import (
    node_search_embedding_hyde,
)
from app.process.query.nodes._10_rrf import node_rrf
from app.process.query.nodes._11_rerank import node_rerank
from app.rag.query.config import RERANK_MAX_TOPK
from app.rag_eval.dataset import ARTIFACTS_DIR, load_batch_eval_cases
from app.rag_eval.metrics import evaluate_query_state
from app.shared.clients import mongo_utils
from app.shared.config.embedding_config import embedding_config
from app.shared.config.milvus_config import milvus_config
from app.shared.config.reranker_config import reranker_config

# 精确率只对最终层有意义: 前几层的"检索条数"是我们自己设的召回池大小, 分母
# (命中数 / K) 因此是人为的 —— 池子设 20 就是 20 分之一, 它测的是池子多大,
# 不是检索准不准. 最终层的条数由断崖截出来, 那才是真正交付给作答链路的列表.
_PRECISION_LAYERS = {"reranked_docs"}

LAYER_LABELS = {
    "embedding_chunks": "普通检索",
    "hyde_embedding_chunks": "HyDE检索",
    "rrf_chunks": "RRF融合",
    "reranked_docs": "最终重排结果",
}


def batch_eval_ready() -> bool:
    """
    判断"批量评测"所需环境是否齐全.

    Milvus / chunk 集合 / item_name 集合 / BGE-M3 四样缺一不可: 前三样决定
    检索跑不跑得起来, 最后一样决定 query 能不能向量化.

    返回值:
    - True: 可以执行批量评测
    - False: 缺少必要配置
    """
    return bool(
        milvus_config.milvus_url
        and milvus_config.chunks_collection
        and milvus_config.item_name_collection
        and (embedding_config.bge_m3_path or embedding_config.bge_m3)
        and reranker_config.bge_reranker_large
    )


def milvus_ready() -> bool:
    """
    判断 MilvusClient 是否已经初始化成功.

    返回值:
    - True: Milvus 可用
    - False: Milvus 不可用
    """
    return infra_milvus.client() is not None


def close_mongo_client() -> None:
    """
    关闭评测过程中可能创建的 Mongo 连接.
    """
    mongo_tool = getattr(mongo_utils, "_history_mongo_tool", None)
    if mongo_tool is not None:
        mongo_tool.client.close()


def run_query_eval_case(case_data: dict) -> dict:
    """
    执行单条问题评测.

    参数:
    - case_data: 单条题库数据, 至少包含:
      - case_id
      - question
      - expected_item_names
      - gold_chunk_ids
      - must_hit_chunk_ids

    返回值:
    - dict: 这道题在 4 层查询链路上的评测结果

    这一层只关心一件事:
    给定一条题库问题, 让真实查询链路完整跑一遍,
    最后把查询结果和标注答案做对比.
    """
    # 1. 构造查询 state. 只给原始问题 —— **不再注入 expected_item_names**:
    # 注进去的话, 主体识别那一层根本没跑, 报告里的"平均主体命中率"是 1.0
    # 这个构造出来的数, 不代表系统能力 (issue C02).
    # 也不注入联网占位数据了: 那是 rerank 强制要求 web 非空时代的补丁,
    # 空值降级落地后这条约束已经不存在, 而假文档会真的进精排池抢名次.
    state = create_query_default_state(
        session_id=f"{case_data['case_id']}_query",
        original_query=case_data["question"],
        rewritten_query=case_data["question"],
        is_stream=False,
    )

    # 2. 真实跑主体识别 (LLM 改写 + 提取 item_names + 拿 item_name 集合确认).
    # 历史读写在这里被替换掉: 评测要的是「给定这句话, 它能不能认出主体」,
    # 不是多轮指代消解; 真读写会让同一题第二次跑的结果和第一次不一样,
    # 也会把评测流量写进线上的 Mongo 历史.
    with (
        patch(
            "app.rag.query.item_name_confirm_service._get_history_by_session_id",
            return_value=[],
        ),
        patch(
            "app.rag.query.item_name_confirm_service._save_user_chat_message",
            return_value=None,
        ),
    ):
        state.update(node_item_name_confirm(state))

    # 3. 主体没确认出来 → 三路召回跑不了. 如实记成"这条没召回"
    # (后面几层全 0), 而不是替它把答案填进去.
    if not state.get("item_names"):
        return evaluate_query_state(
            result_state=state, expected=case_data, rank_k=RERANK_MAX_TOPK
        )

    # 4. 逐层执行真实查询链路.
    # 每个节点执行完后, 结果都会回写到 state 里, 供后续评测计算使用.
    #
    # HyDE 走真实 LLM. 此前这里把它固定成一段占位文字, 想借此消除随机性 ——
    # 但那条路的检索文本是 f"问题: {rewritten_query}, 假设性答案: {hyde_answer}",
    # 问题原文仍在里面, 于是「固定」不但没消掉 HyDE, 反而把它退化成了普通检索的
    # 近似 (报告里两层数字 0.619 / 0.618 几乎相同就是证据), RRF 融合也随之变成
    # 同义反复. 代价是同一道题每次跑结果会有波动 —— 那是真实链路固有的.
    state.update(node_search_embedding(state))
    state.update(node_search_embedding_hyde(state))
    state = node_rrf(state)
    state = node_rerank(state)

    # 6. 用 metrics 模块统一计算这条题的评测结果.
    return evaluate_query_state(
        result_state=state, expected=case_data, rank_k=RERANK_MAX_TOPK
    )


def summarize_eval_results(eval_results: list[dict]) -> dict:
    """
    对多条题目的评测结果做平均汇总.

    参数:
    - eval_results: 所有题目的详细评测结果

    返回值:
    - dict: 整体平均指标
    """
    if not eval_results:
        return {"case_count": 0, "avg_item_name_hit_rate": 0.0, "layers": {}}

    layer_names = (
        "embedding_chunks",
        "hyde_embedding_chunks",
        "rrf_chunks",
        "reranked_docs",
    )
    summary = {
        "case_count": len(eval_results),
        "avg_item_name_hit_rate": round(
            mean(result["item_name_hit_rate"] for result in eval_results), 4
        ),
        "layers": {},
    }

    for layer_name in layer_names:
        summary["layers"][layer_name] = {
            "avg_precision": round(
                mean(
                    result["layers"][layer_name]["precision"] for result in eval_results
                ),
                4,
            ),
            "avg_recall": round(
                mean(result["layers"][layer_name]["recall"] for result in eval_results),
                4,
            ),
            "avg_must_hit_rate": round(
                mean(
                    result["layers"][layer_name]["must_hit_rate"]
                    for result in eval_results
                ),
                4,
            ),
            "avg_mrr_at_k": round(
                mean(
                    result["layers"][layer_name]["mrr_at_k"] for result in eval_results
                ),
                4,
            ),
            "avg_ndcg_at_k": round(
                mean(
                    result["layers"][layer_name]["ndcg_at_k"] for result in eval_results
                ),
                4,
            ),
        }

    return summary


def _to_chinese_case_result(eval_result: dict) -> dict:
    """将单条评测结果转成更适合直接写报告的中文结构.

    两处口径上的取舍:
    1. 题库标注 (`gold_chunk_ids` / `must_hit_chunk_ids`) 是题目自带的, 以前每层
       各带一份, 等于同一份数据抄四遍 —— 现在提到题目级只写一次;
    2. **精确率只给最终层看** —— 前几层的"检索条数"是我们自己设的召回池大小,
       精确率的分母 (命中数 / K) 因此是人为的, 换个池子大小数就变, 它测的是池子
       多大而不是检索准不准. 最终层的条数由断崖截出来, 那才是真正交付的列表.
    """
    chinese_layers = {}
    for layer_name, layer_result in eval_result.get("layers", {}).items():
        layer_block = {
            "检索结果chunk_id列表": layer_result.get("retrieved_chunk_ids", []),
            "检索结果数量": layer_result.get("retrieved_count", 0),
            "命中chunk_id列表": layer_result.get("hit_chunk_ids", []),
            "必须命中结果列表": layer_result.get("must_hit_ids", []),
        }
        if layer_name in _PRECISION_LAYERS:
            layer_block["精确率"] = layer_result.get("precision", 0.0)
        layer_block.update(
            {
                "召回率": layer_result.get("recall", 0.0),
                "必命中率": layer_result.get("must_hit_rate", 0.0),
                # @K 跟着系统的返回条数上限走 (见 evaluate_query_state 的说明)
                f"MRR@{RERANK_MAX_TOPK}": layer_result.get("mrr_at_k", 0.0),
                f"NDCG@{RERANK_MAX_TOPK}": layer_result.get("ndcg_at_k", 0.0),
            }
        )
        chinese_layers[LAYER_LABELS.get(layer_name, layer_name)] = layer_block

    return {
        "用例ID": eval_result.get("case_id", ""),
        "问题": eval_result.get("question", ""),
        "预期主体列表": eval_result.get("expected_item_names", []),
        "识别主体列表": eval_result.get("predicted_item_names", []),
        "主体命中率": eval_result.get("item_name_hit_rate", 0.0),
        "标注相关chunk_id列表": eval_result.get("gold_chunk_ids", []),
        "必须命中chunk_id列表": eval_result.get("must_hit_chunk_ids", []),
        "分层结果": chinese_layers,
    }


def _to_chinese_summary(summary: dict) -> dict:
    """
    将汇总结果转成中文结构, 方便直接展示或落报告.
    """
    chinese_layers = {}
    for layer_name, layer_summary in summary.get("layers", {}).items():
        layer_block = {}
        if layer_name in _PRECISION_LAYERS:
            layer_block["平均精确率"] = layer_summary.get("avg_precision", 0.0)
        layer_block.update(
            {
                "平均召回率": layer_summary.get("avg_recall", 0.0),
                "平均必命中率": layer_summary.get("avg_must_hit_rate", 0.0),
                f"平均MRR@{RERANK_MAX_TOPK}": layer_summary.get("avg_mrr_at_k", 0.0),
                f"平均NDCG@{RERANK_MAX_TOPK}": layer_summary.get("avg_ndcg_at_k", 0.0),
            }
        )
        chinese_layers[LAYER_LABELS.get(layer_name, layer_name)] = layer_block

    return {
        "用例总数": summary.get("case_count", 0),
        "平均主体命中率": summary.get("avg_item_name_hit_rate", 0.0),
        "分层汇总": chinese_layers,
    }


# 报告口径. 随数字一起写进报告文件, 免得报告被单独拿走之后没人知道哪个数不能当真.
REPORT_CAVEATS: list[str] = [
    "题库的问句里**已经写了主体名**; 所以「平均主体命中率」量的是"
    "「明说的名字能不能对上库里 item_name 的写法」, 不含「从口语化提问里推断主体」.",
    "主体识别走真实链路 (LLM 改写 + 提取 + item_name 集合确认); 历史读写被替换为空,"
    " 因为要测的是「给定这句话能不能认出主体」, 不是多轮指代消解.",
    "HyDE 走真实 LLM 生成假设性答案 (不再固定输出); 因此同一道题重跑会有波动.",
    "联网那一路不参与评测 (评测对象是本地知识库召回), 不再注入占位文档.",
    "评测库是 4 份项目说明文档, 共 132 个 chunk (BilibiliDownloader 19 / CharPlot 24"
    " / rag_knowledge 45 / rag_text2sql 44), 题数 40 —— 每份文档 10 题.",
    "gold 只标「答案实际所在的 chunk」(1~5 条), 不是「主题相关集合」."
    " 10 道精确题各 1 条; 其余是聚合题 (答案天然分散在多个小节, 如「计划从哪些方面"
    " 改进」, 最多 5 条) —— 聚合题的 recall 是连续取值, 比 gold=1 的 0/1 分布更能"
    " 分出检索质量的高低. must_hit 是其中缺了就答不出来的那一条.",
    "gold/must 存的是 chunk_id, 而 Milvus 的 chunk_id 是**自增主键** —— 重建索引后"
    " 全部重新分配, 题库必须跟着重映射, 否则 gold 会集体指向别的 chunk 而不报错.",
    "索引侧从 2026-10-03 起在每个 chunk 的检索正文首行拼上祖先章节路径"
    " (`## 5. 快速开始 > ### 5.2 环境变量`), 让子块带上它所属的节; 早于该日建的"
    " 索引没有这条路径, 聚合题的召回会偏低.",
    "返回条数由断崖决定 (见 README §6.3): 分数相对头部衰减过多就在那里断开,"
    " 上下限是 RERANK_MIN_TOPK / RERANK_MAX_TOPK.",
    "MRR@K / NDCG@K 的 K 取 RERANK_MAX_TOPK (返回条数上限) —— 固定 @5 会和"
    " 「实际返回几条」脱节: 返回 8 条时第 6~8 位里的命中明明捞到了, 指标却按 0 算.",
    "精确率只报最终层: 前几层的检索条数是我们自己设的召回池 (不是最终交付的列表),"
    " 精确率的分母因此是人为的 —— 池子设多大, 数就是多大分之一, 它测的是池子"
    " 而不是检索准不准. 最终层的条数由断崖截出来, 才代表真正交给作答链路的内容.",
]


def save_batch_eval_report(
    eval_results: list[dict],
    summary: dict,
    report_name: str | None = None,
) -> Path:
    """
    保存批量评测报告.

    报告里同时放两部分:
    1. 汇总结果;
    2. 每道题的详细结果.

    参数:
    - eval_results: 每道题的详细评测结果
    - summary: 整体汇总结果
    - report_name: 报告文件名; 不传时自动带上时间戳
      (`eval_report_YYYYmmdd_HHMMSS.json`)

    返回值:
    - Path: 报告文件路径

    为什么要时间戳: 口径或配置一变, 两组数字就不可比 —— 覆盖式写盘会让「上一版
    到底多少分」变成查无对证 (这一轮改分块、改题库、改返回条数, 每次都覆盖了上一版).
    """
    if not report_name:
        report_name = f"eval_report_{datetime.now():%Y%m%d_%H%M%S}.json"

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ARTIFACTS_DIR / report_name
    report_path.write_text(
        json.dumps(
            {
                # 口径随数字一起走 —— 报告被单独拿走时, 这几句不能掉队.
                "口径说明": REPORT_CAVEATS,
                "汇总结果": _to_chinese_summary(summary),
                "详细结果": [
                    _to_chinese_case_result(result) for result in eval_results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def run_batch_eval(case_list: list[dict] | None = None) -> dict:
    """
    执行整批评测.

    参数:
    - case_list: 可选, 自定义题库; 不传时默认从题库文件读取

    返回值:
    - eval_results: 每条题目的详细结果
    - summary: 整体平均指标
    - report_path: 报告文件路径

    流程按顺序就是:
    1. 检查环境;
    2. 读取题库;
    3. 逐题调用 `run_query_eval_case()`;
    4. 汇总指标;
    5. 保存报告;
    6. 返回结果.
    """
    if not batch_eval_ready():
        raise RuntimeError("缺少批量评测依赖配置, 无法执行批量检索评测. ")
    if not milvus_ready():
        raise RuntimeError("MilvusClient 未成功初始化, 无法执行批量检索评测. ")

    real_case_list = case_list if case_list is not None else load_batch_eval_cases()
    if not real_case_list:
        raise RuntimeError("未找到批量评测样本, 请先执行评测数据入库. ")

    # 题库中的 gold/must 标注为当前库中的真实 chunk_id(字符串), 直接与检索结果比对.
    # 逐题跑查询链路, 拿到每一道题的分层评测结果.
    eval_results = []
    for i, case_data in enumerate(real_case_list):
        print(f"{'-' * 50} case-{i + 1}: {case_data['question']} {'-' * 50}")
        eval_results.append(run_query_eval_case(case_data))
    # 再把所有题的结果做平均汇总.
    summary = summarize_eval_results(eval_results)
    # 最后把结果写到报告文件.
    report_path = save_batch_eval_report(eval_results, summary)
    return {
        "eval_results": eval_results,
        "summary": summary,
        "report_path": report_path,
    }
