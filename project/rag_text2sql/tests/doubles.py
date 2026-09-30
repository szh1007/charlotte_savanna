"""测试替身: 不起真库 / 不连真服务的那些假件.

命名与放置对齐仓库里另外两处 (`CharAgent/tests/doubles.py` / `CharApp/tests/`) ——
业务侧不抄一份「假仓储」, 替身一律按模块路径导入.
"""

from __future__ import annotations

from app.models.mysql import ColumnInfoMySQL, TableInfoMySQL
from app.models.qdrant import ColumnInfoQdrant


class FakeStreamWriter:
    """接住 `runtime.stream_writer` 推出来的事件, 供断言."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    def stages(self) -> list[str]:
        return [e["stage"] for e in self.events if "stage" in e]

    def errors(self) -> list[str]:
        return [e["error"] for e in self.events if "error" in e]


class FakeRuntime:
    """只实现节点真正用到的那两个属性.

    `langgraph.runtime.Runtime` 是具体类, 但节点对它只做属性访问 ——
    鸭子类型足够, 不必为了测试去构造一个真的 Runtime.
    """

    def __init__(self, context: dict, writer: FakeStreamWriter | None = None) -> None:
        self.context = context
        self.stream_writer = writer if writer is not None else FakeStreamWriter()


class FakeMetaMysqlRepository:
    """meta 库的只读替身: 只实现 `merge_retrieve` 用到的那三个方法."""

    def __init__(
        self,
        columns: list[ColumnInfoMySQL] | None = None,
        tables: list[TableInfoMySQL] | None = None,
        key_columns: dict[str, list[ColumnInfoMySQL]] | None = None,
    ) -> None:
        self._columns = {column.id: column for column in columns or []}
        self._tables = {table.id: table for table in tables or []}
        self._key_columns = key_columns or {}

    async def get_column_info_by_id(self, col_id: str) -> ColumnInfoMySQL:
        return self._columns[col_id]

    async def get_table_info_by_id(self, table_id: str) -> TableInfoMySQL:
        return self._tables[table_id]

    async def get_key_columns_by_table_id(self, table_id: str) -> list[ColumnInfoMySQL]:
        return self._key_columns.get(table_id, [])


class FakeDwMysqlRepository:
    """dw 库的执行替身: `execute_sql` 要么返回预置结果, 要么按预置抛错."""

    def __init__(
        self, result: list | None = None, error: Exception | None = None
    ) -> None:
        self._result = result if result is not None else []
        self._error = error
        self.executed: list[str] = []

    async def execute_sql(self, sql: str) -> list:
        self.executed.append(sql)
        if self._error is not None:
            raise self._error
        return self._result


# ---------------------------------------------------------------------------
# 构造器: 让用例只写「关心的那几个字段」
# ---------------------------------------------------------------------------


def qdrant_column(
    col_id: str,
    name: str,
    table_id: str,
    *,
    role: str = "dimension",
    examples: list | None = None,
) -> ColumnInfoQdrant:
    return ColumnInfoQdrant(
        id=col_id,
        name=name,
        type="varchar",
        role=role,
        examples=examples if examples is not None else [],
        description=f"{name}的业务含义",
        alias=[],
        table_id=table_id,
    )


def mysql_column(
    col_id: str,
    name: str,
    table_id: str,
    *,
    role: str = "dimension",
    examples: list | None = None,
) -> ColumnInfoMySQL:
    return ColumnInfoMySQL(
        id=col_id,
        name=name,
        type="varchar",
        role=role,
        examples=examples if examples is not None else [],
        description=f"{name}的业务含义",
        alias=[],
        table_id=table_id,
    )


def mysql_table(table_id: str, role: str = "dim") -> TableInfoMySQL:
    return TableInfoMySQL(
        id=table_id, name=table_id, role=role, description=f"{table_id}的描述"
    )


def es_value(value: str, column_id: str, table_id: str) -> dict:
    """一条 ES 取值命中 (`ValueInfoEs`)."""
    return {
        "id": f"{column_id}.{value}",
        "value": value,
        "type": "value",
        "column_id": column_id,
        "column_name": column_id.split(".")[-1],
        "table_id": table_id,
        "table_name": table_id,
    }
