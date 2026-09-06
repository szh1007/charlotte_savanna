from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import (
    ColumnInfoState,
    DataAgentState,
    MetricInfoState,
    TableInfoState,
)
from app.core.log import logger
from app.models.mysql import ColumnInfoMySQL
from app.models.qdrant import ColumnInfoQdrant


async def merge_retrieve(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "合并召回信息"})

    try:
        # 获取数据和上下文
        retrieved_columns = state["retrieved_columns"]
        retrieved_metrics = state["retrieved_metrics"]
        retrieved_values = state["retrieved_values"]
        meta_mr = runtime.context["meta_mysql_repository"]

        retrieved_columns_map: dict[str, ColumnInfoQdrant] = {
            col["id"]: col for col in retrieved_columns
        }

        pad_col_ids: list = []

        # 1.确认 [指标] -> [相关字段] 是否被召回
        for metric in retrieved_metrics:
            for col_id in metric["relevant_columns"]:
                if col_id not in retrieved_columns_map:
                    pad_col_ids.append(col_id)

        for value in retrieved_values:
            col_id = value["column_id"]
            _value = value["value"]

            # 2.确认 [字段取值] -> [字段] 是否被召回
            if col_id not in retrieved_columns_map:
                pad_col_ids.append(col_id)

            # 3.确认 [字段取值] -> [字段取值示例] 是否包含本字段取值
            if _value not in retrieved_columns_map[col_id]["examples"]:
                retrieved_columns_map[col_id]["examples"].append(_value)

        # 补充缺失的字段
        for col_id in pad_col_ids:
            # 查询字段 - mysql格式
            extend_column_mysql = await meta_mr.get_column_info_by_id(col_id)

            # mysql格式 -> qdrant格式
            extend_column_qdrant = _convert_column_to_qdrant(extend_column_mysql)

            # 补充到召回字段 retrieved_columns_map
            retrieved_columns_map[col_id] = extend_column_qdrant

        # 构建 [表] -> [字段] 的归属关系映射
        table_to_columns_map: dict[str, list[ColumnInfoQdrant]] = {}
        for column in retrieved_columns_map.values():
            table_id = column["table_id"]
            if table_id not in table_to_columns_map:
                table_to_columns_map[table_id] = []
            table_to_columns_map[table_id].append(column)

        # 4.给表结构补充主键和外键
        for table_id in table_to_columns_map:
            # 查询表的主键和外键
            key_columns = await meta_mr.get_key_columns_by_table_id(table_id)

            # 检查主键和外键是否已经在召回字段中
            existing_column_ids = [col["id"] for col in table_to_columns_map[table_id]]
            for key_column in key_columns:
                if key_column.id not in existing_column_ids:
                    key_column_qdrant = _convert_column_to_qdrant(key_column)
                    table_to_columns_map[table_id].append(key_column_qdrant)

        # 5.最终构建 [表] - [字段] 结构的yaml文件
        table_infos: list[TableInfoState] = []
        for table_id, columns in table_to_columns_map.items():
            table_info_mysql = await meta_mr.get_table_info_by_id(table_id)
            table_info = TableInfoState(
                name=table_info_mysql.name,
                role=table_info_mysql.role,
                description=table_info_mysql.description,
                columns=[ColumnInfoState(**col) for col in columns],
            )
            table_infos.append(table_info)

        logger.info(f"表结构合并完成\n{[table['name'] for table in table_infos]}")

        # 6.构建 [指标] 数据结构的yaml文件
        metric_infos = [MetricInfoState(**metric) for metric in retrieved_metrics]
        logger.info(f"指标结构转换完成\n{[metric['name'] for metric in metric_infos]}")

        return {"table_infos": table_infos, "metric_infos": metric_infos}
    except Exception as e:
        logger.error(f"合并召回信息失败: {e!s}")
        raise


def _convert_column_to_qdrant(column: ColumnInfoMySQL):
    return ColumnInfoQdrant(
        id=column.id,
        name=column.name,
        type=column.type,
        role=column.role,
        examples=column.examples,
        alias=column.alias,
        description=column.description,
        table_id=column.table_id,
    )
