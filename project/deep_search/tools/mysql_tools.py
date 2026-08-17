import os

import dotenv
from langchain_core.tools import tool
from mysql.connector import Error, connect
from rich import print as rprint

from ..api.monitor import monitor

"""
工具1: 检索当前库中有哪些表
    表名一定要【见名知意】
工具2: 检索表中的数据结构、字段、表关系
    主外键字段名一定要保持一致, 表之间的外键关联要独立可识别
工具3: 传入 sql 语句, 执行并返回结果
"""

"""
# from mysql.connector import connect, Error

connect(**config)           -> conn
conn.cursor()               -> cursor
cursor.execute(sql)         -> 执行sql
cursor.fetchall()           -> 获取所有结果 [(xx, ), ...]
cursor.fetchone()           -> 获取一条满足的结果
cursor.fetchmany(number)    -> 获取指定条结果
cursor.description          -> 获取查询的元数据-列名
cursor/conn . close()       -> 释放资源
"""

dotenv.load_dotenv()


def get_db_config():
    """获取数据库配置"""
    config = {  # config 中的 key 是固定的
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USERNAME"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_NAME"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL"),
    }

    # 移除 None 值 (核心必要操作)
    config = {k: v for k, v in config.items() if v is not None}

    # 补充: 校验核心配置是否存在 (可选但推荐)
    required_keys = ["user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置: {', '.join(missing_keys)}")

    return config


@tool
def list_table_names():
    """
    检索当前库中有哪些表

    Args:
        None

    Returns:
        如果有可用表 -> 返回 "当前库中有可用的表名: xxx, xxx, ..."
        如果没有可用表 -> 返回 "当前库中没有任何可用的表"
        执行报错 -> 返回 "查询数据库表名报错, 错误信息: ..."
    """
    monitor.report_tool(tool_name="查询数据库表名", args={})

    try:
        with connect(**get_db_config()) as conn, conn.cursor() as cursor:
            cursor.execute("show tables;")
            conn_result = (
                cursor.fetchall()
            )  # [("drugs",), ("inventory",), ("sales_records",)]
            if not conn_result:
                rprint("当前库中没有任何可用的表")
                return "当前库中没有任何可用的表"

            table_names = [row[0] for row in conn_result]
            return f"当前库中有可用的表名: {', '.join(table_names)}"

    except Error as e:
        rprint(f"查询数据库表名报错, 错误信息: {e!s}")
        return f"查询数据库表名报错, 错误信息: {e!s}"


@tool
def show_table_data(table_name: str):
    """
    查询指定表名的数据结构、字段、表关系
    table_name 是指定的表名, 表名通过 list_table_names 工具获取

    Args:
        table_name: 指定的表名

    Returns:
        如果有数据 -> 返回 csv结果
        如果没有数据 -> 返回 f"指定表 {table_name} 数据为空"
        访问报错 -> 返回 f"查询表结构 {table_name} 报错, 错误信息: ..."
    """
    monitor.report_tool(tool_name="查询表的数据结构", args={"表名": table_name})

    try:
        with connect(**get_db_config()) as conn, conn.cursor() as cursor:
            cursor.execute(f"select * from {table_name} limit 100;")
            table_metadata = cursor.description
            if not table_metadata:
                rprint(f"指定表 {table_name} 数据为空")
                return f"指定表 {table_name} 数据为空"

            fields = [item[0] for item in table_metadata]
            table_data = cursor.fetchall()

            fields_str = ",".join(fields)
            table_data_str_list = [",".join(map(str, item)) for item in table_data]
            return f"{fields_str}\n{'\n'.join(table_data_str_list)}"

    except Error as e:
        rprint(f"查询表结构 {table_name} 报错, 错误信息: {e!s}")
        return f"查询表结构 {table_name} 报错, 错误信息: {e!s}"


@tool
def excute_sql_data(sql: str):
    """
    执行sql语句, 返回查询结果, sql就是要执行的语句
    通过 list_table_names 工具校验表名称
    通过 show_table_data 工具校验表的数据结构、字段、表关系

    Args:
        sql: 要执行的sql语句

    Returns:
        如果有数据 -> 返回 csv结果
        如果没有数据 -> 返回 f"执行查询 {sql} 数据为空"
        访问报错 -> 返回 f"执行查询 {sql} 报错, 错误信息: ..."
    """
    monitor.report_tool(tool_name="执行自定义SQL语句", args={"sql": sql})

    try:
        with connect(**get_db_config()) as conn, conn.cursor() as cursor:
            cursor.execute(sql)
            table_metadata = cursor.description
            if not table_metadata:
                rprint(f"执行查询 {sql} 数据为空")
                return f"执行查询 {sql} 数据为空"

            fields = [item[0] for item in table_metadata]
            table_data = cursor.fetchall()

            fields_str = ",".join(fields)
            table_data_str_list = [",".join(map(str, item)) for item in table_data]
            return f"{fields_str}\n{'\n'.join(table_data_str_list)}"

    except Error as e:
        rprint(f"执行查询 {sql} 报错, 错误信息: {e!s}")
        return f"执行查询 {sql} 报错, 错误信息: {e!s}"


if __name__ == "__main__":
    rprint(list_table_names.invoke({}))
    rprint(show_table_data.invoke({"table_name": "inventory"}))
    rprint(excute_sql_data.invoke({"sql": "select * from inventory limit 100"}))
