from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql import ColumnInfoMySQL, TableInfoMySQL


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
