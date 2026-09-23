"""上下文压缩 (ticket 18) 在**业务这一侧**的样子.

框架那件事 (`agent/compaction.py` 的投影 + `LoopGuard` 之外的这一层治理) 自己有专门
的用例; 这一页断的是**业务接上了没有**, 以及接上之后**哪两件事没跟着变**:

1. **五个旋钮翻译得对** —— `ContextConfig` 的字段逐个落到框架策略的字段上. 这一条
   拦的是本仓吃过一次的那类静默失效: 「开关解析了、存下了、但没生效」(`thinking`
   就这么漏过一次, 由代码评审抓出来的).
2. **经过装配之后真的会压** —— 用一个极小阈值把压缩逼出来, 断言**第二次请求发给
   模型的那份消息**里没有第一句了 (证据落在 wire 上, 不是落在"配置对象非空"上).
3. **账本与用户看到的历史一条不少** —— 压的只是这一次请求的输入, 会话历史
   (`session.history`) 与写进记录表的那些行照旧是全量 (PRD 的「用户看到的记录
   永不压缩」).

模型与商城全走替身 (MockLLM + 内存快照 + respx 假商城), 离线可跑 —— 与
`test_recording.py` 同一套写法.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from conftest import BUYER_ID

from CharAgent.agent import AnchorTokenCounter, TrimAndSummarize
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.stream import EventType, StreamEvent
from CharAgent.tests.mock_llm import MockLLM, text_response
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import ContextConfig
from CharApp.minimall.service import (
    TENANT_WEB,
    MinimallService,
    build_compaction_for,
    build_context,
)

# 一个"必压"的配置: 阈值 1 (估算是按 token 算的, 一份 system 提示就不止 1), 留 1 轮
# 完整对话, 水位线 0.5 (它的作用只是给出停手线; 阈值这么小时一刀就到底了).
FORCING = ContextConfig(max_tokens=1, keep_turns=1, watermark=0.5)


def service_for(
    client: MinimallClient, model: Any, compaction: ContextConfig | None = None
) -> MinimallService:
    """按生产那条线装一份零件 (差别只在快照是替身、压缩参数直接给)."""
    return MinimallService(
        client=client,
        model=model,
        saver=InMemoryCheckpointSaver(),
        compaction=compaction,
    )


async def ask_twice(service: MinimallService, model: MockLLM) -> tuple[list[Any], Any]:
    """同一个会话连问两句; 返回 (收到的事件, 会话).

    第二句是关键的那句: 只有它到来之后账本里才有「一个完整的旧提问」可裁
    (第一句时还没有可裁的段, 压不动).
    """
    events: list[StreamEvent] = []
    context = build_context(BUYER_ID, "web", tenant_id=TENANT_WEB)
    session = await service.session_for(context, event_sink=events.append, redact=False)
    await session.ask("第一句: 订单到哪了")
    await session.ask("第二句: 那什么时候能到")
    return events, session


def compactions(events: list[StreamEvent]) -> list[dict[str, Any]]:
    """这次会话里发生过的压缩事件 (载荷逐条)."""
    return [event.data for event in events if event.type is EventType.CONTEXT_COMPACTED]


def sent_to_model(model: MockLLM, index: int = -1) -> list[str]:
    """模型某一次收到的消息正文 (默认最后一次 —— 摘要是另一次调用, 夹在中间)."""
    return [str(message.get("content")) for message in model.calls[index]["messages"]]


# ---------------------------------------------------------------------------
# 五个旋钮 → 框架的那两个零件
# ---------------------------------------------------------------------------


def test_the_knobs_map_onto_the_framework_policy() -> None:
    """逐个字段对上 (含**估算器**: 它与策略必须成对给, 见 `build_compaction_for`)."""
    compactor, counter = build_compaction_for(
        ContextConfig(
            max_tokens=1500, keep_turns=2, tool_limit=50, summary=False, watermark=0.3
        )
    )

    assert isinstance(compactor, TrimAndSummarize)
    assert isinstance(counter, AnchorTokenCounter)
    assert (compactor.threshold_tokens, compactor.keep_recent_questions) == (1500, 2)
    assert (compactor.watermark_ratio, compactor.tool_result_limit) == (0.3, 50)
    assert compactor.summarize is False


# ---------------------------------------------------------------------------
# 经过装配之后真的会压
# ---------------------------------------------------------------------------


async def test_the_budget_reaches_the_session(client: MinimallClient) -> None:
    """配了阈值 → 第二次请求发给模型的**就是视图**: 第一句已经不在了.

    证据落在 wire 上 (模型收到的消息) 而不是「配置对象非空」上 —— 后者正是那种
    骗得过测试、骗不过用户的断言.

    同时断言**账本一条不少**: 压的是这一次请求的输入, 会话历史 (用户看到的那份)
    仍留着第一句. 这两条必须一起看 —— 只断前一条的话, 「把历史原地删了」也能过.
    """
    model = MockLLM.fixed(text_response("好的"))
    service = service_for(client, model, FORCING)

    events, session = await ask_twice(service, model)

    assert len(compactions(events)) == 1, "第二次问话时就该压一次"
    assert "第一句: 订单到哪了" not in sent_to_model(model), "旧提问该被裁掉"
    assert "第二句: 那什么时候能到" in sent_to_model(model), "正在答的那句必须留着"
    assert any(
        message.get("content") == "第一句: 订单到哪了" for message in session.history
    ), "账本是全量的: 用户看到的历史一条不少"


async def test_the_summary_switch_reaches_the_session(
    client: MinimallClient,
) -> None:
    """摘要开关进到事件里: 开着 `summarized=True`, 关掉 `False` (且不调模型).

    摘要那一步是压缩里唯一花钱的地方, 「关掉」必须真的关掉 —— 事件载荷里的
    `summarized` 就是框架对这一次压缩的如实回答.
    """
    on_model = MockLLM.fixed(text_response("早前聊的是查订单"))
    on_service = service_for(client, on_model, FORCING)

    on_events, _ = await ask_twice(on_service, on_model)

    assert compactions(on_events)[0]["summarized"] is True, "开着就该有摘要"

    off_model = MockLLM.fixed(text_response("早前聊的是查订单"))
    off_service = service_for(client, off_model, replace(FORCING, summary=False))

    off_events, _ = await ask_twice(off_service, off_model)

    payload = compactions(off_events)[0]
    assert payload["summarized"] is False, "关掉就不该有摘要"
    assert payload["warning"] is None, "关掉是配置, 不是降级 (别报一句失败)"
    assert payload["dropped"] > 0, "裁剪照做 (关掉的只是第三件套)"


async def test_without_a_config_nothing_is_compacted(
    client: MinimallClient,
) -> None:
    """`context=None` = 不压缩: 每一轮照旧把全量账本发出去 (与从前逐字一样).

    这条钉的是那个默认值: 装配处不给压缩配置时, 框架那边 `compactor=None` 走的
    是不投影那条路 —— 会话能问答, 只是账本一直长.
    """
    model = MockLLM.fixed(text_response("好的"))
    service = service_for(client, model, None)

    events, _ = await ask_twice(service, model)

    assert compactions(events) == []
    assert "第一句: 订单到哪了" in sent_to_model(model), "没压就该原样发全量账本"
