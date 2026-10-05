"""主体确认的三分支: 确定 / 候选 / 无主体 —— 以及阈值 0.70 / 0.60 的判据.

三分支各自决定完全不同的动作: 有确定主体才更新 item_names 去检索; 只有候选时
**不检索**, 把候选摆进 answer; 两者皆无则作答「未检测到任何主体」. 阈值只差
一个等号 (`>` 与 `>=`) 就会整档滑落, 而拿中间值 (比如 0.8 / 0.5) 测不出这种
漂移 —— 所以边界值 (0.70 / 0.6999 / 0.60 / 0.5999) 是这里的主角.
"""

from __future__ import annotations

from app.rag.query import item_name_confirm_service
from app.rag.query.config import (
    ITEM_NAME_CANDIDATE_THRESHOLD,
    ITEM_NAME_CONFIRM_THRESHOLD,
)


def _entries(*pairs: tuple[str, float]) -> list[dict]:
    """构造一条 milvus 检索结果: (item_name, score) 对."""
    return [{"item_name": name, "score": score} for name, score in pairs]


# ---------------------------------------------------------------------------
# 阈值三分支 (纯函数: 直接喂 milvus_search_result)
# ---------------------------------------------------------------------------


def test_score_at_the_confirm_threshold_is_confirmed():
    """0.70 整要算「确定」—— 判据是 `>=`, 差一个等号就掉进候选分支."""
    result = item_name_confirm_service._select_confirm_candidate_item_names(
        {"甲": _entries(("设备A", ITEM_NAME_CONFIRM_THRESHOLD))}
    )

    assert result == {"confirm": ["设备A"], "candidate": []}


def test_score_just_below_the_confirm_threshold_falls_to_candidate():
    """0.6999 不是确定, 是候选 —— 差一点点也不许直接去检索."""
    result = item_name_confirm_service._select_confirm_candidate_item_names(
        {"甲": _entries(("设备A", ITEM_NAME_CONFIRM_THRESHOLD - 0.0001))}
    )

    assert result == {"confirm": [], "candidate": ["设备A"]}


def test_score_at_the_candidate_threshold_is_still_a_candidate():
    """0.60 整要算「候选」—— 判据是 `>=`, 再低一档就什么都不剩了."""
    result = item_name_confirm_service._select_confirm_candidate_item_names(
        {"甲": _entries(("设备A", ITEM_NAME_CANDIDATE_THRESHOLD))}
    )

    assert result == {"confirm": [], "candidate": ["设备A"]}


def test_score_below_the_candidate_threshold_is_dropped():
    """0.5999 连候选都不算 —— 它该走「未检测到主体」, 而不是被硬塞给用户."""
    result = item_name_confirm_service._select_confirm_candidate_item_names(
        {"甲": _entries(("设备A", ITEM_NAME_CANDIDATE_THRESHOLD - 0.0001))}
    )

    assert result == {"confirm": [], "candidate": []}


def test_empty_search_result_falls_into_the_no_subject_branch():
    """向量库为空 (没有查到任何 item_name) 时同样两个列表都空, 不是报错."""
    result = item_name_confirm_service._select_confirm_candidate_item_names({})

    assert result == {"confirm": [], "candidate": []}


def test_both_lists_respect_their_topk():
    """确定列表只留 top1, 候选列表只留 top2 —— 顺序是相关度顺序, 超出的不要.

    数量取多了会把低分主体也写进 item_names 去检索, 等于把「确定」的语义
    稀释成「差不多都行」.
    """
    result = item_name_confirm_service._select_confirm_candidate_item_names(
        {
            "甲": _entries(("高1", 0.9), ("高2", 0.8)),
            "乙": _entries(("候1", 0.65), ("候2", 0.64), ("候3", 0.63)),
        }
    )

    assert set(result["confirm"]) == {"高1"}
    assert set(result["candidate"]) == {"候1", "候2"}


# ---------------------------------------------------------------------------
# _change_state_property: 三个分支各自往 state 里写什么
# ---------------------------------------------------------------------------


def test_confirm_branch_updates_retrieval_keys_and_beats_candidates():
    """有确定主体就写 item_names + rewritten_query, 不再写 answer —— 去检索.

    同一轮里还有候选时也一样: 候选只是兜底, 不该和确定主体一起改变动作.
    """
    state = {}

    item_name_confirm_service._change_state_property(
        state, "改写后的问题", {"confirm": ["设备A"], "candidate": ["设备B"]}
    )

    assert state["item_names"] == ["设备A"]
    assert state["rewritten_query"] == "改写后的问题"
    assert "answer" not in state, "有确定主体时不该同时作答"


def test_candidate_only_branch_answers_without_touching_retrieval():
    """只有候选时不检索: 不写 item_names, 不改写, 把候选名单摆进 answer."""
    state = {}

    item_name_confirm_service._change_state_property(
        state, "改写后的问题", {"confirm": [], "candidate": ["设备A", "设备B"]}
    )

    assert "item_names" not in state, "候选分支绝不能悄悄去检索"
    assert "rewritten_query" not in state
    assert state["answer"].startswith("未检测到明确的主体")
    assert "设备A" in state["answer"] and "设备B" in state["answer"]


def test_no_subject_branch_answers_with_the_prompt_to_check_the_kb():
    """两者皆无时作答「未检测到任何主体」—— 让用户去管理员处核对知识库."""
    state = {}

    item_name_confirm_service._change_state_property(
        state, "改写后的问题", {"confirm": [], "candidate": []}
    )

    assert state["answer"] == "未检测到任何主体, 请向管理员确认知识库的内容"
    assert "item_names" not in state


# ---------------------------------------------------------------------------
# 节点接线: 三分支在 confirm_item_name 整体里各自走到哪一步
# ---------------------------------------------------------------------------


def _patch_node_seams(monkeypatch, llm_result: dict, milvus_dict: dict):
    """把节点里的历史 / LLM / Milvus / 消息存档全部换成假的, 返回三本记录簿."""
    seen: dict[str, list] = {"milvus_names": [], "saved": [], "milvus_calls": 0}

    def fake_select_milvus(item_names):
        seen["milvus_calls"] += 1
        seen["milvus_names"].append(list(item_names))
        return milvus_dict

    monkeypatch.setattr(
        item_name_confirm_service, "_get_history_by_session_id", lambda sid: []
    )
    monkeypatch.setattr(
        item_name_confirm_service,
        "_call_llm_rewritten_and_extract_itemnames",
        lambda query, histories: dict(llm_result),
    )
    monkeypatch.setattr(
        item_name_confirm_service, "_select_item_names_milvus", fake_select_milvus
    )
    monkeypatch.setattr(
        item_name_confirm_service,
        "_save_user_chat_message",
        lambda state: seen["saved"].append(dict(state)),
    )
    return seen


def test_confirm_node_wires_the_confirmed_names_into_the_state(monkeypatch):
    """整条节点走通: LLM 提取的名字进检索, 确定命中写回 state, 提问被存档."""
    seen = _patch_node_seams(
        monkeypatch,
        llm_result={"rewritten_query": "改写后", "item_names": ["设备"]},
        milvus_dict={"设备": _entries(("设备A", 0.9))},
    )

    state = item_name_confirm_service.confirm_item_name(
        {"session_id": "s", "original_query": "这机器怎么保养"}
    )

    assert seen["milvus_names"] == [["设备"]], "LLM 提取的名字要原样进检索"
    assert state["item_names"] == ["设备A"]
    assert state["rewritten_query"] == "改写后"
    assert "answer" not in state
    assert seen["saved"][0]["item_names"] == ["设备A"], "存档要带上确认出的主体"


def test_confirm_node_stops_retrieval_when_only_candidates(monkeypatch):
    """只有候选的整条节点: 不走检索, state 里出现的是一条候选作答."""
    seen = _patch_node_seams(
        monkeypatch,
        llm_result={"rewritten_query": "改写后", "item_names": ["设备"]},
        milvus_dict={"设备": _entries(("设备A", 0.65))},
    )

    state = item_name_confirm_service.confirm_item_name(
        {"session_id": "s", "original_query": "这机器怎么保养"}
    )

    assert "item_names" not in state
    assert "设备A" in state["answer"]
    assert "item_names" not in seen["saved"][0]


def test_confirm_node_skips_milvus_when_the_llm_extracted_nothing(monkeypatch):
    """LLM 一个主体都没提取到时连 Milvus 都不该查 —— 空输入查库没有意义."""
    seen = _patch_node_seams(
        monkeypatch,
        llm_result={"rewritten_query": "改写后", "item_names": []},
        milvus_dict={},
    )
    state = item_name_confirm_service.confirm_item_name(
        {"session_id": "s", "original_query": "你好"}
    )

    assert seen["milvus_calls"] == 0, "没有主体时不该去查 Milvus"
    assert state["answer"] == "未检测到任何主体, 请向管理员确认知识库的内容"
