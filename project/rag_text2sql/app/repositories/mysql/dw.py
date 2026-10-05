from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import DBInfoState
from app.core.log import logger
from app.core.sql_guard import (
    GuardNote,
    SqlGuardLimits,
    SqlRejectedError,
    enforce_limit,
    own_limits,
    readonly_violation,
)


class DwMysqlRepository:
    """dw 数据库操作.

    执行的是**模型生成的 SQL**, 所以两条通往数据库的路 (`validate_sql` 校验 /
    `execute_sql` 取数) 都从这里过护栏 —— 白名单拒绝非只读语句, 放行的按上限
    补/收紧 LIMIT (判据见 `app/core/sql_guard.py`).图的 `_7_validate_sql` 与
    `_9_execute_sql` 都调这两个方法, 于是**一处实现覆盖两个节点**.

    其余几个方法 (`get_column_types` / `get_column_values` / `get_db_info`) 是
    内部管理读 (如 `SHOW COLUMNS`), **不走护栏**: 它们不是模型生成的 SQL, 而
    包裹会毁掉 `SHOW` 这类语句.

    数据库侧另有一层兜底 (引擎连接建立时设置, 见 `app/clients/mysql.py`):
    只读事务 + `max_execution_time` —— 白名单万一漏了, 写操作报 1792, 慢查询报 3024.
    """

    def __init__(
        self,
        session: AsyncSession,
        limits: SqlGuardLimits = SqlGuardLimits(),
    ):
        self.session = session
        self.limits = limits
        self.notes: list[GuardNote] = []
        self.last_note: GuardNote | None = None

    # ------------------------------------------------------------------
    # 内部管理读: 不过护栏
    # ------------------------------------------------------------------

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        """
        获取指定表的字段名称和类型的字典

        Args:
            table_name: 表名称

        Returns:
            字段名称和类型的字典
        """
        sql = f"SHOW COLUMNS from {table_name}"
        result = await self.session.execute(text(sql))
        return {row.Field: row.Type for row in result.fetchall()}

    async def get_column_values(
        self,
        table_name: str,
        column_name: str,
        limit: int = 10,
    ) -> list[str]:
        """
        查询当前字段的取值示例

        Args:
            table_name: 表名称
            column_name: 字段名称
            limit: 取值示例数量, 默认 10

        Returns:
            字段取值示例列表
        """
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        result = await self.session.execute(text(sql))
        return result.scalars().fetchall()  # scalars() 以列表形式返回单列数据

    async def get_db_info(self) -> DBInfoState:
        """
        查询数据库的版本和方言
        """
        result = await self.session.execute(text("SELECT VERSION()"))
        version = result.scalar()

        dialect = self.session.get_bind().dialect.name
        return DBInfoState(version=version, dialect=dialect)

    # ------------------------------------------------------------------
    # 模型生成的 SQL: 过护栏
    # ------------------------------------------------------------------

    async def validate_sql(self, sql: str) -> None:
        """
        校验 SQL 语句是否有效 (执行一次, 不取结果)

        Args:
            sql: SQL 语句

        Raises:
            SqlRejectedError: 语句过不了只读白名单
        """
        await self._guarded(sql, fetch=False)

    async def execute_sql(self, sql: str) -> list:
        """
        执行 SQL 语句并返回结果

        Args:
            sql: SQL 语句

        Returns:
            执行结果

        Raises:
            SqlRejectedError: 语句过不了只读白名单
        """
        return await self._guarded(sql, fetch=True)

    def _record(self, note: GuardNote) -> None:
        self.last_note = note
        self.notes.append(note)

    async def _guarded(self, sql: str, *, fetch: bool):
        reason = readonly_violation(sql)
        if reason is not None:
            self._record(
                GuardNote(rejected_reason=reason, own_limits=(), applied_limit=None)
            )
            # 拒绝理由是给**模型**看的可操作文本; 也是给运维看的: 这一行说明
            # 「模型写出过非只读语句」, 哪怕最后没执行
            logger.warning(f"SQL 被只读护栏拒绝 (未下发数据库)\n{reason}\n{sql}")
            raise SqlRejectedError(reason)

        # 护栏在**文本层面**补/收 LIMIT, 不做外层包裹 (包裹会把合法 SQL 弄坏,
        # 见 `enforce_limit` 的说明)
        guarded_sql, enforced = enforce_limit(sql, self.limits)
        note = GuardNote(
            rejected_reason=None, own_limits=own_limits(sql), applied_limit=enforced
        )
        self._record(note)
        logger.info(f"SQL 过护栏: {note.describe()}")

        result = await self._execute_resilient(text(guarded_sql))
        if not fetch:
            return None
        return [dict(row) for row in result.mappings().fetchall()]

    async def _execute_resilient(self, statement):
        """跑一条语句; 只在**连接失效**时重试一次.

        语句错误 (语法错 / 未知列 / 被 max_execution_time 掐断) 一律不重试 ——
        那是 SQL 本身的问题, 重试只会再错一次; 它该走校正节点 (错误原文透传,
        模型照着改).
        """
        try:
            return await self.session.execute(statement)
        except DBAPIError as exc:
            if not exc.connection_invalidated:
                raise
            logger.warning(f"数据库连接失效, 回滚后重试一次: {exc!s}")
            await self.session.rollback()
            return await self.session.execute(statement)
