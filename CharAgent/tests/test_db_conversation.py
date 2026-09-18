"""会话消息分层的自检 (不需要数据库): 谁该给前端看, 谁不该.

这是消息分层这条验收的核心防线. 两条规则各自有专门的用例:

1. **分层**: 一问一答可见, 内部件 (系统续写指令 / 工具回填 / 带工具调用的中间轮)
   不可见 —— 尤其要防「冒充用户说的消息」被渲染成用户真的说过.
2. **最终答复取 `LoopResult.content`** (而不是 messages 数组的最后一条): 截断续写
   (CONTINUE) 时两者完全不同, 抄尾段等于把答案拦腰截断交给用户.

第 2 条是这批用例里最要紧的一条 —— 它在真实场景下才暴露 (要正好赶上截断), 而
一旦错了, 用户看到的就是半句话.
"""

from __future__ import annotations

from CharAgent.db.conversation import (
    TurnPair,
    assistant_answer,
    conversation_turns,
    count_visible,
    visible_transcript,
)


def _user(text: str) -> dict:
    """造一条用户消息 (wire 形状)."""
    return {"role": "user", "content": text}


def _assistant(text: str, *, reasoning: str | None = None, calls: list | None = None):
    """造一条 assistant 消息 (可选带思维链与工具调用)."""
    message: dict = {"role": "assistant", "content": text}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "query_order", "arguments": "{}"},
            }
            for call_id in calls
        ]
    return message


def test_user_question_is_visible():
    """用户真说的那句给前端看."""
    lines = visible_transcript([_user("订单到哪了")])

    assert len(lines) == 1
    assert lines[0].visible is True
    assert lines[0].content == "订单到哪了"


def test_plain_assistant_answer_is_visible():
    """不带工具调用的 assistant 消息给前端看 (它在回答, 不是在做过程叙述)."""
    lines = visible_transcript([_assistant("已发货")])

    assert lines[0].visible is True


def test_tool_calling_turn_is_hidden():
    """带 tool_calls 的中间轮藏起来 —— 那是「让我先查一下」, 不是答案."""
    lines = visible_transcript([_assistant("让我先查一下", calls=["call_0"])])

    assert lines[0].hidden is True
    assert lines[0].tool_call_ids == ["call_0"]


def test_tool_result_is_hidden():
    """工具回填藏起来 —— 那是喂模型的原料, 用户看的是结论."""
    lines = visible_transcript([{"role": "tool", "content": "订单已发货"}])

    assert lines[0].hidden is True


def test_system_message_is_hidden():
    """系统注入的指令 (续写 / 摘要 / 角色设定) 藏起来 —— 系统说的, 不是人说的."""
    lines = visible_transcript(
        [{"role": "system", "content": "接着上面继续写, 不要重复"}]
    )

    assert lines[0].hidden is True


def test_a_full_tool_turn_keeps_only_the_qa_pair_visible():
    """一整轮 (问 → 中间轮 → 工具 → 答) 走一遍: 只有问与答可见.

    这是最常见的一条完整路径, 也是分层的总验收 —— 前端点开一段对话该看到什么,
    就是这个形状.
    """
    wire = [
        _user("我的订单到哪了"),
        _assistant("让我先查一下", calls=["call_0"]),
        {"role": "tool", "content": "订单已发货, 单号 SF123"},
        _assistant("您的订单已发货, 单号 SF123"),
    ]

    lines = visible_transcript(wire)

    assert [line.visible for line in lines] == [True, False, False, True]
    assert count_visible(lines) == 2


def test_continuation_answer_uses_loop_result_content_not_last_message():
    """**截断续写场景: 答复必须取 `LoopResult.content`, 不能抄 messages 尾条**.

    真实形状 (CONTINUE 策略): 模型写到一半被 token 上限截断, 框架保留前缀并让它
    接着写, 于是最终答复跨好几条 assistant 消息:

        assistant "……巨龙蜿蜒如"   (被截断的前缀)
        system    "接着上面继续写" (框架注入的续写指令)
        assistant "巨龙般盘踞"     (续写的尾段)

    `messages[-1]["content"]` 只是**尾段**; 框架拼好的 `LoopResult.content` 才是
    完整答复. 这条用例把两者写成不同值, 然后断言拿到的是拼合后的那个 —— 抄错
    来源就会在这里红.
    """
    wire = [
        _user("描述一下昆仑山"),
        _assistant("昆仑山绵延千里, 山势蜿蜒如", reasoning="先写地理"),
        {"role": "system", "content": "接着上面继续写, 不要重复"},
        _assistant("巨龙般盘踞在西部", reasoning="续写形态"),
    ]
    joined = "昆仑山绵延千里, 山势蜿蜒如巨龙般盘踞在西部"

    answer, reasoning = assistant_answer(joined, wire)

    assert answer == joined
    assert answer != wire[-1]["content"], "抄了尾段 —— 这正是要防的错误"
    # 思维链要跨段拼起来 (两段各有各的思考过程, 只留最后一段就是残缺的)
    assert reasoning == "先写地理\n续写形态"


def test_answer_can_be_none_when_nothing_was_produced():
    """没答出正文时如实返回 None (guard 刹车 / 上游中断那种收尾).

    返回空串会把「没答」伪装成「答了空」, 前端就没法渲染「这次没答出来」.
    """
    wire = [_user("退款"), _assistant("", calls=["call_0"])]

    answer, reasoning = assistant_answer(None, wire)

    assert answer is None
    assert reasoning is None


def test_conversation_turns_yields_one_pair_for_a_simple_turn():
    """一次普通问答收成**一条** TurnPair."""
    wire = [
        _user("订单到哪了"),
        _assistant("让我查一下", calls=["call_0"]),
        {"role": "tool", "content": "已发货"},
        _assistant("已经发货了"),
    ]

    turns = conversation_turns(wire, "已经发货了")

    assert len(turns) == 1
    assert turns[0].question == "订单到哪了"
    assert turns[0].answer == "已经发货了"


def test_conversation_turns_keeps_only_the_answer_on_the_last_question():
    """历史里有多个问题时, 只有最后一问带上本次的最终答复.

    前面几问的答案在它们各自的 messages 里, 而 `content` 只说本次. 硬把同一个
    content 套到每一问上就是张冠李戴 —— 这条用例钉住「只有最后一问带答复」.
    """
    wire = [
        _user("第一问"),
        _assistant("第一答"),
        _user("第二问"),
        _assistant("第二答"),
    ]

    turns = conversation_turns(wire, "第二答")

    assert [turn.question for turn in turns] == ["第一问", "第二问"]
    assert [turn.answer for turn in turns] == [None, "第二答"]


def test_since_skips_already_stored_messages():
    """`since` 只收新增的那一段 (老消息上一次已经存过了, 重存会写出重复行).

    调用方知道「跑之前历史有多长」, 传进来即可 —— 这是「同一个会话多次运行,
    每次只落新增部分」的实现方式.
    """
    prior = [_user("第一问"), _assistant("第一答")]
    wire = [*prior, _user("第二问"), _assistant("第二答")]

    turns = conversation_turns(wire, "第二答", since=len(prior))
    lines = visible_transcript(wire, since=len(prior))

    assert [turn.question for turn in turns] == ["第二问"]
    assert turns[0].answer == "第二答"
    assert [line.content for line in lines] == ["第二问", "第二答"]


def test_since_beyond_history_is_treated_as_empty():
    """`since` 比历史还长时当作「没有新增」, 而不是报错或负索引.

    负下标会让最后几条老消息被当成新消息重存一遍 —— 悄悄写出重复数据比报错难
    查得多.
    """
    wire = [_user("只有一问")]

    assert conversation_turns(wire, "答", since=99) == []
    assert visible_transcript(wire, since=99) == []


def test_negative_since_is_clamped_to_zero():
    """负数 `since` 按 0 处理 (调用方算错时不该少收内容)."""
    wire = [_user("问"), _assistant("答")]

    turns = conversation_turns(wire, "答", since=-5)

    assert len(turns) == 1


def test_text_free_history_yields_no_pairs():
    """一条用户消息都没有 (纯系统触发 / 工具续跑) 时返回空列表.

    没有「问」就谈不上「答」—— 这种内容不进会话历史 (它在快照里, 归 transcript).
    """
    wire = [
        {"role": "system", "content": "系统触发"},
        _assistant("我主动说点什么"),
    ]

    assert conversation_turns(wire, "我主动说点什么") == []
    assert count_visible(visible_transcript(wire)) == 1


def test_model_invented_user_line_is_not_attributed_to_the_user():
    """模型自己编出的「用户说…」是 assistant 消息, 绝不能渲染成用户真的说过.

    wire 里只有一条真 user (用户问的是「我的订单」); 模型在回复里虚构了一句
    「用户说: 我要投诉」. 由于后者在 wire 里是 assistant 角色, 它会跟其他
    assistant 消息一样走「在回答」那条路 —— 而**取问题时只看 role == user**,
    于是拿去当问题的只有用户真说的那句.

    这条是正确性问题而非排版问题: 把模型编的话安在用户头上, 客户端与服务端会
    同时被判 bug.
    """
    wire = [
        {"role": "user", "content": "我的订单到哪了"},
        _assistant("用户说: 我要投诉。好的, 我帮您登记。"),
    ]

    turns = conversation_turns(wire, "用户说: 我要投诉。好的, 我帮您登记。")

    assert [turn.question for turn in turns] == ["我的订单到哪了"]


def test_malformed_history_entries_are_skipped_not_crashed():
    """历史里的坏数据 (不是字典 / content 是数字 / tool_calls 形状不对) 不炸.

    历史可能是调用方手工拼的, 一条坏数据不该让整段会话读不出来 —— 看不见的
    条目藏起来即可.
    """
    wire = [
        "这不是字典",
        {"role": "user", "content": 12345},
        {"role": "assistant", "content": "答", "tool_calls": "不是列表"},
        _user("真问题"),
        _assistant("真答案"),
    ]

    lines = visible_transcript(wire)
    turns = conversation_turns(wire, "真答案")

    assert [line.visible for line in lines] == [False, True, True, True, True]
    assert [turn.question for turn in turns] == ["", "真问题"]


def test_turn_pair_is_a_plain_dataclass():
    """TurnPair 是纯数据 (问题 / 答复 / 思维链), 落库时由仓储补编号与归属.

    这条钉住的是「分层逻辑不碰数据库」: 它能被单独测、被复用, 不需要真库.
    """
    turn = TurnPair(question="问", answer="答", reasoning="想")

    assert (turn.question, turn.answer, turn.reasoning) == ("问", "答", "想")
    assert TurnPair(question="问", answer=None).reasoning is None
