from sqlalchemy import Select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql import (
    ColumnInfoMySQL,
    ColumnMetricMySQL,
    MetricInfoMySQL,
    TableInfoMySQL,
)


class MetaMysqlRepository:
    """meta 数据库 CRUD 操作"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_table_infos(self, table_infos: list[TableInfoMySQL]):
        """
        保存表信息 table_info 到 meta 数据库
        先清空旧数据再全量写入, 保证重复构建幂等

        Args:
            table_infos: 所有表的信息列表
        """
        await self.session.execute(delete(TableInfoMySQL))
        self.session.add_all(table_infos)

    async def save_column_infos(self, column_infos: list[ColumnInfoMySQL]):
        """
        保存字段信息 column_info 到 meta 数据库
        先清空旧数据再全量写入, 保证重复构建幂等

        Args:
            column_infos: 所有表的所有字段信息列表
        """
        await self.session.execute(delete(ColumnInfoMySQL))
        self.session.add_all(column_infos)

    async def save_metric_infos(self, metric_infos: list[MetricInfoMySQL]):
        """
        保存指标信息 metric_info 到 meta 数据库
        先清空旧数据再全量写入, 保证重复构建幂等

        Args:
            metric_infos: 所有指标的信息列表
        """
        await self.session.execute(delete(MetricInfoMySQL))
        self.session.add_all(metric_infos)

    async def save_column_metrics(self, column_metrics: list[ColumnMetricMySQL]):
        """
        保存指标关联信息 column_metric 到 meta 数据库
        先清空旧数据再全量写入, 保证重复构建幂等

        Args:
            column_metrics: 所有指标的所有关联信息列表
        """
        await self.session.execute(delete(ColumnMetricMySQL))
        self.session.add_all(column_metrics)

    async def get_column_info_by_id(self, col_id: str) -> ColumnInfoMySQL:
        """
        从 meta 数据库中查询字段信息 column_info

        Args:
            col_id: 字段 id

        Returns:
            ColumnInfoMySQL: 字段信息
        """
        return await self.session.get(ColumnInfoMySQL, col_id)

    async def get_table_info_by_id(self, table_id: str) -> TableInfoMySQL:
        """
        从 meta 数据库中查询表信息 table_info

        Args:
            table_id: 表 id

        Returns:
            TableInfoMySQL: 表信息
        """
        return await self.session.get(TableInfoMySQL, table_id)

    async def get_key_columns_by_table_id(
        self,
        table_id: str,
    ) -> list[ColumnInfoMySQL]:
        """
        从 meta 数据库中查询表的主键和外键

        Args:
            table_id: 表 id

        Returns:
            list[ColumnInfoMySQL]: 表的主键和外键列表
        """
        sql = """
            select *
            from column_info
            where table_id = :table_id
            and role in ('primary_key', 'foreign_key')
        """
        query = Select(ColumnInfoMySQL).from_statement(text(sql))
        result = await self.session.execute(query, {"table_id": table_id})
        return result.scalars().fetchall()
