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

import argparse
import json
from collections.abc import Callable
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
from app.rag.query import hyde_search_service, rerank_service
from app.rag.query.config import RERANK_MAX_TOPK
from app.rag_eval.dataset import (
    ARTIFACTS_DIR,
    get_frozen_answer,
    load_batch_eval_cases,
    load_hyde_answers,
    put_frozen_answer,
    save_hyde_answers,
)
from app.rag_eval.metrics import evaluate_query_state, mean_must_hit_rate
from app.shared.clients import mongo_utils
from app.shared.config.embedding_config import embedding_config
from app.shared.config.milvus_config import milvus_config
from app.shared.config.reranker_config import reranker_config

# 真实的 HyDE 假设答案生成函数 —— **在 import 期就把引用拿住**.
# 抓取那一趟 `hyde_search_service` 里这个名字会被 patch 成"回调本模块", 按名字去取
# 只会取到 patch 后的那个 (自己调自己). 这份引用不受 patch 影响.
_real_hyde_llm = hyde_search_service._call_llm_by_rewritten_query

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


def _arm_name(rerank: bool) -> str:
    """臂名 —— **只有这一个来源**.

    它同时进追踪 ID (`latency.py` 靠它分组) 与报告 (`compare.py` 靠它分组), 两处
    必须永远一致. 早先 `run_query_eval_case` 有一个默认 `arm="rerank-on"` 的形参,
    与这里派生的值是两个来源 —— 传错了不报错, 只是报告的臂名和日志的臂名对不上.
    """
    return f"rerank-{'on' if rerank else 'off'}"


def _hyde_answer_for(
    case_id: str,
    question: str,
    rewritten_query: str,
    frozen: dict[str, dict[str, str]],
    may_generate: bool,
    generate: Callable[[str], str],
) -> str:
    """取这道题的 HyDE 假设答案 —— 优先用冻结的那份.

    **为什么要冻结**: HyDE 这一路只有一处 LLM 调用 (生成假设性答案), 之后全是确定
    性的向量检索. 不冻的话, 同一道题两次跑会生成不同的假设答案, 于是检索文本不同、
    候选池不同、精排看到的 10 条也不同 —— 而本票要量的恰恰是「精排开/关」两臂之差,
    臂内方差被 LLM 抖动喂大之后, 两臂之差就归因不到精排头上.

    这与 C02 处理过的「固定成占位文字」**不是一回事**: 那时候是把假答案塞进去, 而检索
    文本里还拼着原问题, HyDE 于是退化成了普通检索的近似; 现在存的是**这道题真实生成
    出来的假设答案**, 只是不再每次重采. 代价是那一次采样的好坏被固化 —— 如实记在
    README §6.3.

    同款手法在 rag_text2sql 那边已经用过 (C18: 冻结关键词扩展结果重放).

    参数:
    - question: **当前**题库里的问题原文. 冻结的那份要拿它比一下 —— 题目改过了,
      旧的假设答案就是答非所问 (见 `dataset.get_frozen_answer`).
    - generate: 真实生成函数, **由调用方显式传进来**. 不在这里按名字去模块上取 ——
      抓取那一趟 `hyde_search_service` 里这个名字正被 patch 成"回调本函数", 按名字取
      会取到 patch 后的那个, 于是自己调自己 (实测 RecursionError, 且被 HyDE 那一路的
      `isolate_route` 吞掉, 表现为静默降级成空召回).
    """
    cached = get_frozen_answer(frozen, case_id, question)
    if cached is not None:
        return cached
    if not may_generate:
        raise KeyError(
            f"题库里有 {case_id}, 但 hyde_answers.json 里没有它**当题**的冻结假设答案"
            f" (没抓过, 或者题目改过). 先补齐: "
            f"python -m app.rag_eval.runner --capture-hyde"
        )
    answer = generate(rewritten_query)
    put_frozen_answer(frozen, case_id, question, answer)
    return answer


def run_query_eval_case(
    case_data: dict,
    hyde_answers: dict[str, dict[str, str]] | None = None,
    rerank: bool = True,
) -> dict:
    """
    执行单条问题评测.

    参数:
    - case_data: 单条题库数据, 至少包含:
      - case_id
      - question
      - expected_item_names
      - gold_chunk_ids
      - must_hit_chunk_ids
    - hyde_answers: 逐题冻结的 HyDE 假设答案 (取用前会比对当前问题)
    - rerank: 精排开关. 消融实验的另一臂就靠它 —— 关掉时按 RRF 顺序截取候选

    返回值:
    - dict: 这道题在 4 层查询链路上的评测结果

    **臂名为什么要进追踪 ID**: 两臂跑的是同一批题, 若不区分, `@node_log` 打出的
    追踪 ID 完全相同, 而逐节点耗时汇总 (`latency.py`) 是"后写覆盖" —— 同一天跑完
    两臂, 汇总里只剩后跑那一臂, 且**不会报错**. 带上臂名之后两臂各自成组,
    耗时对照才拿得到数.
    """
    frozen_hyde = {} if hyde_answers is None else hyde_answers
    # 1. 构造查询 state. 只给原始问题 —— **不再注入 expected_item_names**:
    # 注进去的话, 主体识别那一层根本没跑, 报告里的"平均主体命中率"是 1.0
    # 这个构造出来的数, 不代表系统能力 (issue C02).
    # 也不注入联网占位数据了: 那是 rerank 强制要求 web 非空时代的补丁,
    # 空值降级落地后这条约束已经不存在, 而假文档会真的进精排池抢名次.
    state = create_query_default_state(
        session_id=f"{case_data['case_id']}_query@{_arm_name(rerank)}",
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

    # 3.5 回放模式到这里就该有冻结答案了 —— 缺了就当场报错.
    # 抓取是**独立的一步** (`capture_hyde_answers`), 不在评测链路里做: 那条路要跑完
    # 检索 + 精排 (实测精排 15 秒/题), 只为了拿一次 LLM 的结果, 纯属白烧 20 分钟.
    frozen_now = get_frozen_answer(
        frozen_hyde, case_data["case_id"], case_data["question"]
    )
    if frozen_now is None:
        raise KeyError(
            f"题库里有 {case_data['case_id']}, 但 hyde_answers.json 里没有它**当题**的"
            f"冻结假设答案 (没抓过, 或者题目改过). 先补齐: "
            f"python -m app.rag_eval.runner --capture-hyde"
        )

    # 4. 逐层执行真实查询链路.
    # 每个节点执行完后, 结果都会回写到 state 里, 供后续评测计算使用.
    #
    # HyDE 那一路的**假设答案走冻结重放**, 检索本身仍打真实 Milvus:
    # 只把 LLM 那一次调用换掉, 见 `_hyde_answer_for`.
    state.update(node_search_embedding(state))
    with patch.object(
        hyde_search_service,
        "_call_llm_by_rewritten_query",
        side_effect=lambda rewritten_query: _hyde_answer_for(
            case_data["case_id"],
            case_data["question"],
            rewritten_query,
            frozen_hyde,
            False,
            _real_hyde_llm,
        ),
    ):
        state.update(node_search_embedding_hyde(state))
    state = node_rrf(state)
    # 精排开关: 只改这一处的模块级常量, 不动生产代码 —— `rerank_documents` 每次
    # 调用都现查这个名字, patch 生效. 「关」臂按 RRF 顺序截取 RERANK_MAX_TOPK 条
    # (不精排、也不过断崖), 正是票面定义的对照臂.
    with patch.object(rerank_service, "ENABLE_RERANK", rerank):
        state = node_rerank(state)

    # 6. 用 metrics 模块统一计算这条题的评测结果.
    return evaluate_query_state(
        result_state=state, expected=case_data, rank_k=RERANK_MAX_TOPK
    )


def _mean_or_none(values: list[float | None]) -> float | None:
    """只在"适用"的题上取平均; 一道适用的都没有时返回 None."""
    applicable = [v for v in values if v is not None]
    return sum(applicable) / len(applicable) if applicable else None


def _round_or_none(value: float | None) -> float | None:
    """保留 None 的"不适用"语义 —— 不能顺手 round 成 0, 那会把"没标"报成"没打中"."""
    return None if value is None else round(value, 4)


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
        # 没标预期主体的题 (拒答题) 记 None, 不参与分母 —— 与必命中率同一套语义
        "avg_item_name_hit_rate": _round_or_none(
            _mean_or_none([result["item_name_hit_rate"] for result in eval_results])
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
            # 必命中率只在"标了关键 chunk"的题上平均, 没标的题不参与分母 ——
            # 否则「该答不知道」那类题会凭空把它拉低 (见 metrics.mean_must_hit_rate)
            "avg_must_hit_rate": _round_or_none(
                mean_must_hit_rate(
                    [
                        result["layers"][layer_name]["must_hit_rate"]
                        for result in eval_results
                    ]
                )
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
    "**跨题库的数字不可直接比**: 语料 / 分块 / 题库 / 交付条数任一变化, 两组数字就"
    " 不在一把尺子上. 引用历史数字前先看它的语料版本 (报告自带这一节说明).",
    "评测库是 15 篇政府公开文本 (网络与数据合规: 3 部法律 + 3 部行政法规 + 9 部部门"
    " 规章), 612 条 / 约 7.8 万字 / 138 个 chunk; 题库 79 题. 出处见"
    " `assets/eval_corpus/SOURCES.md`.",
    "**题库的问句大多没有点明主体**(57/79) —— 那正是真实用户的问法"
    " (「网络日志留存多久」而不是「网络安全法规定网络日志留存多久」). 主体识别因此分"
    " 两步: 先看问题里有没有专有名称, 没有再拿问题去**全库 chunk 召回**反推它在问哪份"
    " 文档 (低于阈值则如实返回「未检测到主体」). 所以「平均主体命中率」量的是这整条"
    " 识别链路, 不只是「明说的名字对不对得上」.",
    "主体识别走真实链路 (LLM 改写 + 提取 + item_name 集合确认 + 内容兜底); 历史读写"
    " 被替换为空, 因为要测的是「给定这句话能不能认出主体」, 不是多轮指代消解.",
    "HyDE 的假设答案是**冻结重放**的 (artifacts/hyde_answers.json): 逐题只生成一次,"
    " 重跑复用; 检索仍打真实 Milvus, 冻的只是那一次 LLM 调用. 因此同一道题重跑不含"
    " LLM 抖动 —— 两臂之差可归因到精排本身.",
    "联网那一路不参与评测 (评测对象是本地知识库召回), 不再注入占位文档.",
    "gold 只标「答案实际所在的 chunk」, 不是「主题相关集合」; must_hit 是其中"
    "「缺了这道题就答不出来」的关键块, 可为 0~N 条.",
    "必命中率是**最低门槛**: 标注了关键块的题, 捞到任意一条就算过 (不是「捞到了几成」)."
    " 一条都没标的题 (「该答不知道」那类) 记 null, 汇总时排除出分母 —— 既不算打中"
    " 也不算打漏.",
    "gold/must 存的是 chunk_id, 而 Milvus 的 chunk_id 是**自增主键** —— 重建索引后"
    " 全部重新分配. 题库源存的是**可读文本片段**, 由 `scripts/build_eval_cases.py`"
    " 现解析成 id —— 否则每次重切分都要人工重标整本, 而且标错了不报错.",
    "相邻块之间有 50 字 overlap, 落在重叠区的句子会同时出现在两块里 —— **两块都算"
    " 正确检索**, 都收进了 gold.",
    "交付条数取 top-1 (`RERANK_MAX_TOPK = 1`): 实测交付 2 条时第 2 条是答案的概率只有"
    " 约 1/3, 多带一条只换回约 5 个点召回却砍掉一半精确率. 断崖截断因此不再参与"
    " (上限等于下限), 判据与参数留着以备放宽.",
    "MRR@K / NDCG@K 的 K 取 RERANK_MAX_TOPK (交付条数上限) —— 它是跟着配置变的,"
    " 字段名会变 (当前 `MRR@1`). 固定 @5 会和「实际交付几条」脱节.",
    "精确率只报最终层: 前几层的检索条数是我们自己设的召回池 (不是最终交付的列表),"
    " 精确率的分母因此是人为的 —— 池子设多大, 数就是多大分之一, 它测的是池子"
    " 而不是检索准不准. **精确率与交付条数是同一对刻度**: 读精确率之前先看这一层"
    " 交付了几条 (报告里每层都带 `检索结果数量`).",
]


def save_batch_eval_report(
    eval_results: list[dict],
    summary: dict,
    report_name: str | None = None,
    extra_caveats: list[str] | None = None,
    run_config: dict | None = None,
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
    - extra_caveats: 本次运行特有的口径说明, 排在最前面 (比如"这一趟的 HyDE
      假设答案是现场生成的") —— 数字离开当次上下文就没法判读, 口径得跟着走

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
                # 结构化记录这一趟跑的什么 —— 消融的对照脚本靠它分组, 不靠文件名猜
                "运行配置": run_config or {},
                # 口径随数字一起走 —— 报告被单独拿走时, 这几句不能掉队.
                "口径说明": [*(extra_caveats or []), *REPORT_CAVEATS],
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


def capture_hyde_answers(case_list: list[dict] | None = None) -> int:
    """
    把题库里还缺的 HyDE 假设答案补齐 (题库新增题目后跑一次).

    **为什么不顺手放在评测链路里抓**: 那条路要为每道题跑完检索 + 精排 (实测精排
    15 秒/题), 而这里只需要一次 LLM 调用 —— 79 道题差着二十分钟. 抓取只是把
    "每次重跑都要现生成"换成"只生成一次", 跟检索、精排没有任何关系.

    取 `rewritten_query` 要走一次主体识别 (HyDE 的输入是改写后的问题), 历史读写
    同样替换为空 (与评测口径一致).

    **每一道题都要冻, 包括主体没认出来的那些** —— 那一趟它们确实走不到 HyDE, 但主体
    识别是整条链上唯一还在调的 LLM: 万一回放时它认出了主体, 这道题就会走到 HyDE 而
    发现没有冻结答案, 于是**整批报错中断**. 冻上就没有这个翻车点 (多存几条不用的
    答案, 代价可以忽略).

    返回值:
    - int: 本次新增的条数
    """
    cases = case_list if case_list is not None else load_batch_eval_cases()
    frozen = load_hyde_answers()
    added = 0

    for case_data in cases:
        if get_frozen_answer(frozen, case_data["case_id"], case_data["question"]):
            continue
        state = create_query_default_state(
            session_id=f"{case_data['case_id']}_capture",
            original_query=case_data["question"],
            rewritten_query=case_data["question"],
            is_stream=False,
        )
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
        _hyde_answer_for(
            case_data["case_id"],
            case_data["question"],
            state["rewritten_query"],
            frozen,
            True,
            _real_hyde_llm,
        )
        added += 1
        print(f"  冻结 {case_data['case_id']}: {case_data['question'][:30]}")

    if added:
        path = save_hyde_answers(frozen)
        print(f"新增 {added} 条 HyDE 假设答案 -> {path}")
    return added


def run_batch_eval(
    case_list: list[dict] | None = None,
    capture_hyde: bool = False,
    rerank: bool = True,
    report_name: str | None = None,
) -> dict:
    """
    执行整批评测.

    参数:
    - case_list: 可选, 自定义题库; 不传时默认从题库文件读取
    - capture_hyde: 先**补齐冻结的 HyDE 假设答案**再开跑. 题库新增题目后带上这个开关
      跑一次即可; 补齐之后本趟就是纯冻结重放, 数字照常可用.
    - rerank: 精排开关 —— 消融的另一臂. 关掉时跳过精排与断崖, 按 RRF 顺序取前 N 条.
    - report_name: 报告文件名; 不传时自动带时间戳. 消融跑批时建议显式给名
      (如 `eval_arm_off_run1.json`), 免得四份报告的先后靠时间戳猜.

    返回值:
    - eval_results: 每条题目的详细结果
    - summary: 整体平均指标
    - report_path: 报告文件路径
    - run_config: 这一趟的运行配置 (臂 / HyDE 口径 / 题数)

    流程按顺序就是:
    1. 检查环境;
    2. 读取题库与冻结的 HyDE 假设答案;
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

    if capture_hyde:
        added = capture_hyde_answers(real_case_list)
        print(f"补齐了 {added} 条 HyDE 假设答案" if added else "冻结文件已是齐的")

    hyde_answers = load_hyde_answers()
    arm = _arm_name(rerank)
    run_config = {
        "臂": arm,
        "rerank": rerank,
        "hyde": "frozen-replay",
        "题数": len(real_case_list),
        "冻结的假设答案数": len(hyde_answers),
    }

    # 题库中的 gold/must 标注为当前库中的真实 chunk_id(字符串), 直接与检索结果比对.
    # 逐题跑查询链路, 拿到每一道题的分层评测结果.
    eval_results = []
    for i, case_data in enumerate(real_case_list):
        print(f"{'-' * 50} case-{i + 1}: {case_data['question']} {'-' * 50}")
        eval_results.append(run_query_eval_case(case_data, hyde_answers, rerank))

    # 再把所有题的结果做平均汇总.
    summary = summarize_eval_results(eval_results)
    # 最后把结果写到报告文件.
    report_path = save_batch_eval_report(
        eval_results,
        summary,
        report_name=report_name,
        extra_caveats=[
            f"运行配置: {run_config}",
            _hyde_caveat(len(hyde_answers)),
        ],
        run_config=run_config,
    )
    return {
        "eval_results": eval_results,
        "summary": summary,
        "report_path": report_path,
        "run_config": run_config,
    }


def _hyde_caveat(frozen_count: int) -> str:
    """本次运行的 HyDE 口径 —— 抓取与回放两趟的数字不能混着比."""
    return (
        f"HyDE 假设答案走**冻结重放** (artifacts/hyde_answers.json, 共 "
        f"{frozen_count} 条): 同一道题重跑时假设答案完全一致, 因此臂内方差里不含 "
        f"LLM 抖动 —— 两臂之差可以归因到精排本身. 检索仍打真实 Milvus, "
        f"冻结的只是那一次 LLM 调用."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量检索评测 (C17)")
    parser.add_argument(
        "--capture-hyde",
        action="store_true",
        help="抓取模式: 现场生成 HyDE 假设答案并冻结落盘 (题库新增题目后跑一次)",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="消融的另一臂: 跳过精排与断崖, 按 RRF 顺序取前 N 条",
    )
    parser.add_argument(
        "--report-name",
        default=None,
        help="报告文件名 (消融跑批时显式给名, 免得靠时间戳猜先后)",
    )
    args = parser.parse_args()

    result = run_batch_eval(
        capture_hyde=args.capture_hyde,
        rerank=not args.no_rerank,
        report_name=args.report_name,
    )
    print(f"\n运行配置: {result['run_config']}")
    print(f"报告落盘: {result['report_path']}")
