"""C01 回归: `merge_retrieve` 的「取值→列」兜底路径.

原来的顺序是「收集缺字段 → 回填 examples → 补列」, 而回填 examples 要读
`retrieved_columns_map[col_id]` —— 那个 col_id 此刻**还没进 map** (补列在后面),
于是 KeyError. 这条兜底路径**写了从未真正生效过**: 日志里「合并召回信息失败」0 次.

修法是把补列挪到回填之前. 下面两个用例是**分叉的两个输入**: 值命中的列
「已被列召回」(老代码能跑) 与「未被列召回」(老代码崩) —— 只测后者的话,
前一条路回归了也看不出来.
"""

from __future__ import annotations

import pytest
from doubles import (
    FakeMetaMysqlRepository,
    FakeRuntime,
    es_value,
    mysql_column,
    mysql_table,
    qdrant_column,
)

from app.agent.nodes._3_merge_retrieve import merge_retrieve

BRAND_COLUMN_ID = "dim_product.brand"
AMOUNT_COLUMN_ID = "fact_order.order_amount"


def _state(recalled_columns, values):
    return {
        "query": "上个月华为品牌卖了多少",
        "retrieved_columns": recalled_columns,
        "retrieved_metrics": [],
        "retrieved_values": values,
    }


def _columns_of(result, table_name):
    tables = {table["name"]: table for table in result["table_infos"]}
    return {column["name"]: column for column in tables[table_name]["columns"]}


async def test_value_hit_whose_column_was_not_recalled_is_padded_instead_of_crashing():
    """回归本体: 值命中的列没进列召回 → 补列, 不是 KeyError."""
    recalled = [
        qdrant_column(AMOUNT_COLUMN_ID, "订单金额", "fact_order", role="measure")
    ]
    values = [es_value("华为", BRAND_COLUMN_ID, "dim_product")]
    repository = FakeMetaMysqlRepository(
        columns=[mysql_column(BRAND_COLUMN_ID, "品牌", "dim_product")],
        tables=[mysql_table("dim_product"), mysql_table("fact_order", role="fact")],
    )

    result = await merge_retrieve(
        _state(recalled, values), FakeRuntime({"meta_mysql_repository": repository})
    )

    columns = _columns_of(result, "dim_product")
    assert "品牌" in columns, "缺的列应当被补进结果, 而不是崩掉"
    assert columns["品牌"]["examples"] == ["华为"], "命中的取值要回填进该列的示例"


async def test_value_hit_whose_column_was_recalled_appends_the_example():
    """另一条分叉: 列本来就在召回集里 (老代码走的就是这条)."""
    recalled = [
        qdrant_column(BRAND_COLUMN_ID, "品牌", "dim_product", examples=["小米"]),
        qdrant_column(AMOUNT_COLUMN_ID, "订单金额", "fact_order", role="measure"),
    ]
    values = [es_value("华为", BRAND_COLUMN_ID, "dim_product")]
    repository = FakeMetaMysqlRepository(
        tables=[mysql_table("dim_product"), mysql_table("fact_order", role="fact")]
    )

    result = await merge_retrieve(
        _state(recalled, values), FakeRuntime({"meta_mysql_repository": repository})
    )

    columns = _columns_of(result, "dim_product")
    assert columns["品牌"]["examples"] == ["小米", "华为"]


async def test_an_already_present_example_is_not_duplicated():
    recalled = [
        qdrant_column(BRAND_COLUMN_ID, "品牌", "dim_product", examples=["华为"]),
    ]
    values = [es_value("华为", BRAND_COLUMN_ID, "dim_product")]
    repository = FakeMetaMysqlRepository(tables=[mysql_table("dim_product")])

    result = await merge_retrieve(
        _state(recalled, values), FakeRuntime({"meta_mysql_repository": repository})
    )

    assert _columns_of(result, "dim_product")["品牌"]["examples"] == ["华为"]


async def test_pushes_the_stage_event_before_the_work_can_fail():
    """名字里的 "before" 要被真的钉住: 阶段事件先推, 后面出错也已发出去.

    若把 writer 挪到 try 之后, 失败时前端连阶段名都收不到 —— 而那正是 SSE 契约
    要保的东西: 出事了至少知道它停在哪一步.
    """

    class BoomRepository(FakeMetaMysqlRepository):
        async def get_column_info_by_id(self, col_id: str):
            raise RuntimeError("meta 库连不上")

    runtime = FakeRuntime({"meta_mysql_repository": BoomRepository()})
    values = [es_value("华为", BRAND_COLUMN_ID, "dim_product")]

    with pytest.raises(RuntimeError, match="meta 库连不上"):
        await merge_retrieve(_state([], values), runtime)

    assert runtime.stream_writer.stages() == ["合并召回信息"]
