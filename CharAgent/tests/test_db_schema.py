"""表定义的自检 (不需要数据库): 五张表该有的形状都在.

关注点是「表定义有没有漏东西」—— 这些是**离线**能验的:
- 表名都带 `charagent_` 前缀 (共库不撞名的前提)
- 每张表、每一列都有注释 (库里的注释就是文档, 漏一个将来就得去翻代码)
- 五个主键与设计文档一致 (含 tool_calls 的复合主键)
- 外键的删除动作 (CASCADE / SET NULL) 与设计一致
- 索引齐全 (会话列表、历史翻页、按运行查调用)

**与库的实际结构是否一致**由 `test_db_store.py` 的 reflection 对比负责
(那个需要真库) —— 这里只管「定义本身写得对不对」.
"""

from __future__ import annotations

from CharAgent.db.schema import (
    ALL_TABLES,
    TABLE_NAMES,
    checkpoints,
    messages,
    runs,
    threads,
    tool_calls,
)

PREFIX = "charagent_"


def test_every_table_is_prefixed():
    """五张表都带 charagent_ 前缀.

    为什么这条值得单独一个用例: 本项目各子项目**共用同一个 Postgres 库**, 而
    `threads` / `runs` / `messages` 是极常见的表名. `checkpoints` 已经真撞过一次
    (与 langgraph-checkpoint-postgres 同名, 报错信息与真因毫无关系) —— 前缀是
    这件事唯一的结构性防线, 掉了就再撞.
    """
    for name in TABLE_NAMES:
        assert name.startswith(PREFIX), f"{name} 没带前缀"


def test_five_entities_are_declared():
    """五实体齐全 (thread / run / message / tool_call / checkpoint)."""
    assert set(TABLE_NAMES) == {
        "charagent_threads",
        "charagent_runs",
        "charagent_messages",
        "charagent_tool_calls",
        "charagent_checkpoints",
    }
    assert len(ALL_TABLES) == 5


def test_every_column_has_a_comment():
    """每一列都有注释 —— 注释会随建表写进库 (COMMENT ON), 是运维排查时的文档.

    漏一列的检查意义不大, 但「谁来保证不漏」很重要: 加字段时忘了写注释, 三年后
    看库的人只能去翻代码, 而代码可能已经重构过好几轮.
    """
    missing = [
        f"{table.name}.{column.name}"
        for table in ALL_TABLES
        for column in table.columns
        if not column.comment
    ]
    assert not missing, f"这些列没有注释: {missing}"


def test_every_table_has_a_comment():
    """每张表都有表级注释 (与列注释同一条理由: 注释随建表写进库).

    表注释回答的是「这张表装什么」—— 光看表名看不出「runs 与 messages 的分界是
    什么」这类问题, 而库里的注释是运维与后人**第一眼**会看到的东西.
    """
    missing = [table.name for table in ALL_TABLES if not table.comment]
    assert not missing, f"这些表没有表级注释: {missing}"


def test_primary_keys_match_the_design():
    """主键与设计文档一致 (重点是 tool_calls 的三列主键).

    tool_calls 为什么必须三列: 真实上游每轮都从 `call_0` 重新编号 —— 单列在
    第二次调用就撞; 只加 `run_id` 只解决「跨运行」, **同一个 run 的多轮之间**
    照样撞. 真正唯一的身份是「哪条 assistant 消息发起的这一次调用」.
    """
    assert [c.name for c in threads.primary_key.columns] == ["thread_id"]
    assert [c.name for c in runs.primary_key.columns] == ["run_id"]
    assert [c.name for c in messages.primary_key.columns] == ["message_id"]
    assert [c.name for c in tool_calls.primary_key.columns] == [
        "run_id",
        "message_id",
        "tool_call_id",
    ]
    assert [c.name for c in checkpoints.primary_key.columns] == ["checkpoint_id"]


def test_foreign_key_delete_actions():
    """外键的删除动作: 会话删掉时它的运行/消息一并删 (CASCADE), 归属关系断了则置空.

    - `runs.thread_id` / `messages.thread_id` CASCADE: 会话没了, 它的数据不该
      留在库里当孤儿.
    - `messages.run_id` SET NULL: 那次运行记录被清理时, 消息本身还该留着
      (用户看到的历史不该跟着消失).
    - `tool_calls.message_id` CASCADE: 它是 tool_calls 主键的一部分, 主键列不能
      为空, 所以只能跟着走 (发起那条调用的消息没了, 这次调用的记录也不该留).
    - `checkpoints.parent_id` SET NULL: 删一帧时, 挂在它下面的分支**不被连带删掉**
      (只是变成「没有起点的孤儿」)—— 删数据是危险动作, 宁可留下看着奇怪的记录,
      也不要替调用方把下游数据一起抹掉.
    """
    assert _ondelete(messages, "thread_id") == "CASCADE"
    assert _ondelete(runs, "thread_id") == "CASCADE"
    assert _ondelete(tool_calls, "run_id") == "CASCADE"
    assert _ondelete(messages, "run_id") == "SET NULL"
    assert _ondelete(checkpoints, "parent_id") == "SET NULL"
    # tool_calls.message_id 是**主键的一部分**, 所以只能 CASCADE 不能 SET NULL
    # (主键列不允许为空): 发起那条调用的消息没了, 这次调用的记录也跟着走.
    assert _ondelete(tool_calls, "message_id") == "CASCADE"


def test_datetime_columns_are_timezone_aware():
    """所有时间列都带时区.

    排序键全是时间 (取最新一帧 / 翻消息历史), 裸时间戳读回来是「哪儿的几点」都
    不知道的数字 —— 换台机器、跨个时区, 排序就可能悄悄错位.
    """
    naive = [
        f"{table.name}.{column.name}"
        for table in ALL_TABLES
        for column in table.columns
        if column.name.endswith("_at") and not getattr(column.type, "timezone", False)
    ]
    assert not naive, f"这些时间列没带时区: {naive}"


def test_expected_indexes_exist():
    """关键查询路径都有索引 (没有索引的查询在数据上量后会拖垮接口).

    - 会话列表: 按租户/属主 + 最近活动
    - 消息与快照: 按会话翻历史
    - 工具调用: 按运行查这一轮调了什么
    """
    expected = {
        "ix_charagent_threads_tenant_updated",
        "ix_charagent_threads_user_updated",
        "ix_charagent_runs_thread_created",
        "ix_charagent_messages_thread_created",
        "ix_charagent_tool_calls_run_created",
        "ix_charagent_checkpoints_thread_created",
    }
    found = {index.name for table in ALL_TABLES for index in table.indexes}
    assert expected <= found, f"缺这些索引: {expected - found}"


def test_design_doc_index_table_matches_the_code():
    """设计文档里那张索引表与代码逐条一致 (文档漂移由测试兜住).

    为什么值得测: 索引是**会被漏记**的那类东西 —— 加表时顺手加了个索引, 文档忘了
    补; 或者反过来, 文档写着一个早删掉的索引. 两种漂移都不会让代码报错, 只会让
    读文档的人按一份不存在的结构去设计查询.

    这条用例把 `docs/design/02-data-model.md` 的「索引一览」表读出来, 与
    `schema.py` 里的 Index 对象逐条比 (名字 / 表 / 列). 文档改了而代码没改 (或反之)
    都会在这里红.
    """
    import re
    from pathlib import Path

    doc_path = (
        Path(__file__).resolve().parents[1] / "docs" / "design" / "02-data-model.md"
    )
    doc = doc_path.read_text(encoding="utf-8")
    # 取「索引一览」小节到下一个二级标题之间的那段
    section = doc.split("### 索引一览")[1].split("\n## ")[0]
    documented = {
        name: (table, [column.strip() for column in columns.split("+")])
        for name, table, columns in re.findall(
            r"\| `([^`]+)` \| (\w+) \| ([^|]+) \|", section
        )
    }

    in_code = {
        index.name: (
            table.name.removeprefix("charagent_"),
            [column.name for column in index.columns],
        )
        for table in ALL_TABLES
        for index in table.indexes
    }

    assert documented == in_code, (
        "设计文档的索引表与代码不一致 —— "
        f"只在文档里: {set(documented) - set(in_code)}; "
        f"只在代码里: {set(in_code) - set(documented)}"
    )


def _ondelete(table, column_name: str) -> str | None:
    """取某个外键列的 ondelete 动作 (没这条外键则断言失败)."""
    column = table.columns[column_name]
    for key in column.foreign_keys:
        return key.ondelete
    raise AssertionError(f"{table.name}.{column_name} 上没有外键")
