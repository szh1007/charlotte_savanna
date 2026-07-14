"""数据库操作层:连接管理、建表、CRUD、统计查询。

所有 SQL 使用参数化查询(`?` 占位符),禁止字符串拼接。
数据库文件 `db.sqlite3` 位于本模块同级目录,运行时自动创建。
"""

import os
import sqlite3
from typing import Any

# 数据库文件路径:与本模块同级的 db.sqlite3
_DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite3")

# 建表 DDL(与 schema.sql 保持同步)
_DDL = """
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT    NOT NULL CHECK(type IN ('income', 'expense')),
    amount      REAL    NOT NULL CHECK(amount > 0),
    category    TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    note        TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
"""


def get_connection() -> sqlite3.Connection:
    """获取数据库连接。

    Streamlit 多线程环境下,需要 check_same_thread=False 避免线程检查报错。
    调用方负责关闭连接。
    """
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # 查询结果以 dict-like Row 对象返回
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """初始化数据库:建表 + 建索引(IF NOT EXISTS,幂等)。

    每次 Streamlit rerun 时都会被调用,但不会重复创建已有结构。
    """
    conn = get_connection()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


def add_transaction(
    type_: str,
    amount: float,
    category: str,
    date: str,
    note: str = "",
) -> int:
    """新增一条收支记录。

    Args:
        type_: 类型,'income' 或 'expense'。
        amount: 金额,必须 > 0。
        category: 分类。
        date: 日期,格式 YYYY-MM-DD。
        note: 备注,可选。

    Returns:
        新记录的 id。
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO transactions (type, amount, category, date, note) VALUES (?, ?, ?, ?, ?)",
            (type_, amount, category, date, note),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def query_transactions(
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
    type_: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> tuple[list[dict[str, Any]], int]:
    """分页查询账目列表,支持按年/月/分类/类型筛选。

    Args:
        year: 年份,None 表示不筛选。
        month: 月份(1-12),None 表示不筛选。
        category: 分类,None 表示不筛选。
        type_: 类型('income'/'expense'),None 表示全部。
        page: 页码,1-based。
        page_size: 每页条数。

    Returns:
        (当前页数据列表, 总记录数)。每条记录为 dict。
    """
    conditions: list[str] = []
    params: list[Any] = []

    if year is not None:
        conditions.append("strftime('%Y', date) = ?")
        params.append(str(year))
    if month is not None:
        conditions.append("strftime('%m', date) = ?")
        params.append(f"{month:02d}")
    if category is not None:
        conditions.append("category = ?")
        params.append(category)
    if type_ is not None:
        conditions.append("type = ?")
        params.append(type_)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    conn = get_connection()
    try:
        # 查询总条数
        count_sql = f"SELECT COUNT(*) FROM transactions {where_clause}"
        total = conn.execute(count_sql, params).fetchone()[0]

        # 分页查询数据
        offset = (page - 1) * page_size
        data_sql = f"SELECT * FROM transactions {where_clause} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
        rows = conn.execute(data_sql, [*params, page_size, offset]).fetchall()

        # Row 对象转为 dict 列表
        records = [dict(row) for row in rows]
        return records, total
    finally:
        conn.close()


def delete_transactions(ids: list[int]) -> int:
    """批量删除指定 id 的记录。

    Args:
        ids: 待删除的 id 列表。空列表时不执行任何操作。

    Returns:
        实际删除的条数。
    """
    if not ids:
        return 0

    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        cursor = conn.execute(
            f"DELETE FROM transactions WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_category_stats(
    year: int | None = None,
    month: int | None = None,
) -> list[dict[str, Any]]:
    """按分类汇总金额与占比。

    先按 type 分组,再按 category 汇总,计算每个分类在其 type 中的占比。

    Args:
        year: 年份筛选,None 表示全部。
        month: 月份筛选,None 表示全部。

    Returns:
        列表,每项为 {type, category, total, percentage},按 total 降序排列。
    """
    conditions: list[str] = []
    params: list[Any] = []

    if year is not None:
        conditions.append("strftime('%Y', date) = ?")
        params.append(str(year))
    if month is not None:
        conditions.append("strftime('%m', date) = ?")
        params.append(f"{month:02d}")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    conn = get_connection()
    try:
        # 先计算每个 type 的总金额,用于算占比
        type_total_sql = f"SELECT type, SUM(amount) AS type_total FROM transactions {where_clause} GROUP BY type"
        type_totals = {row["type"]: row["type_total"] for row in conn.execute(type_total_sql, params).fetchall()}

        # 按分类汇总
        category_sql = (
            f"SELECT type, category, SUM(amount) AS total FROM transactions {where_clause} "
            "GROUP BY type, category ORDER BY total DESC"
        )
        rows = conn.execute(category_sql, params).fetchall()

        stats: list[dict[str, Any]] = []
        for row in rows:
            type_total = type_totals.get(row["type"], 1)
            percentage = round(row["total"] / type_total * 100, 1) if type_total > 0 else 0.0
            stats.append(
                {
                    "type": row["type"],
                    "category": row["category"],
                    "total": row["total"],
                    "percentage": percentage,
                }
            )
        return stats
    finally:
        conn.close()


def get_years() -> list[int]:
    """获取所有存在记录的年份(降序),用于筛选下拉框。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT strftime('%Y', date) AS year FROM transactions ORDER BY year DESC"
        ).fetchall()
        return [int(row["year"]) for row in rows]
    finally:
        conn.close()
