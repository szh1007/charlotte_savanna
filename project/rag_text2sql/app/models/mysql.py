from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class TableInfoMySQL(Base):
    __tablename__ = "table_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="表编号")
    name: Mapped[str | None] = mapped_column(String(128), comment="表名称")
    role: Mapped[str | None] = mapped_column(String(32), comment="表类型(fact/dim)")
    description: Mapped[str | None] = mapped_column(Text, comment="表描述")


class ColumnInfoMySQL(Base):
    __tablename__ = "column_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="列编号")
    name: Mapped[str | None] = mapped_column(String(128), comment="列名称")
    type: Mapped[str | None] = mapped_column(String(64), comment="数据类型")
    role: Mapped[str | None] = mapped_column(
        String(32), comment="列类型(primary_key,foreign_key,measure,dimension)"
    )
    examples: Mapped[dict | list | None] = mapped_column(JSON, comment="数据示例")
    description: Mapped[str | None] = mapped_column(Text, comment="列描述")
    alias: Mapped[dict | list | None] = mapped_column(JSON, comment="列别名")
    table_id: Mapped[str | None] = mapped_column(String(64), comment="所属表编号")


class MetricInfoMySQL(Base):
    __tablename__ = "metric_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="指标编码")
    name: Mapped[str | None] = mapped_column(String(128), comment="指标名称")
    description: Mapped[str | None] = mapped_column(Text, comment="指标描述")
    relevant_columns: Mapped[dict | list | None] = mapped_column(
        JSON, comment="关联字段"
    )
    alias: Mapped[dict | list | None] = mapped_column(JSON, comment="指标别名")


class ColumnMetricMySQL(Base):
    __tablename__ = "column_metric"

    column_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="列编号"
    )
    metric_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="指标编号"
    )
