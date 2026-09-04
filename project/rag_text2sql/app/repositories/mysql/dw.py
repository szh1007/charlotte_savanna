from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DwMysqlRepository:
    """dw 数据库 CRUD 操作"""

    def __init__(self, session: AsyncSession):
        self.session = session

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
