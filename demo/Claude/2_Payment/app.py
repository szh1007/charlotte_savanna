"""个人记账工具 — Streamlit 主入口。

启动方式：
    streamlit run Claude/2_Payment/app.py

页面结构：
    侧边栏导航 → 3 个功能页面（添加记录 / 查看列表 / 分类统计）
"""

import hashlib
import json

import streamlit as st

from constants import EXPENSE_CATEGORIES, INCOME_CATEGORIES, PAGE_SIZE
from database import (
    add_transaction,
    delete_transactions,
    get_category_stats,
    get_years,
    init_db,
    query_transactions,
)
from charts import render_bar_chart, render_stats_table
from ui_components import (
    render_add_form,
    render_batch_delete_button,
    render_filter_bar,
    render_pagination,
    render_single_delete_confirm,
    render_transaction_list,
)

# ---- 页面配置 ---------------------------------------------------------------

st.set_page_config(
    page_title="个人记账",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- 全局 CSS ---------------------------------------------------------------

def _inject_css() -> None:
    """注入全局自定义样式。"""
    st.markdown("""
    <style>
    /* ===== 根变量 ===== */
    :root {
        --color-income: #059669;
        --color-income-bg: #ecfdf5;
        --color-expense: #dc2626;
        --color-expense-bg: #fef2f2;
        --color-border: #e5e7eb;
        --radius: 10px;
        --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    }

    /* ===== 全局 ===== */
    .stMainBlockContainer h1, .stMainBlockContainer h2, .stMainBlockContainer h3 {
        font-weight: 700;
    }

    /* ===== 空状态 ===== */
    .empty-state {
        text-align: center;
        padding: 60px 20px;
        border: 2px dashed #e5e7eb;
        border-radius: var(--radius);
        background: #fafafa;
    }

    /* ===== 分页 ===== */
    .pagination-bar {
        background: #f8fafc;
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 10px 16px;
        margin: 16px 0;
    }
    .pagination-info {
        text-align: center;
        padding-top: 5px;
        font-size: 14px;
        color: #374151;
    }
    .pagination-total {
        font-size: 12px;
        color: #6b7280;
    }

    /* ===== 批量删除 ===== */
    .batch-delete-bar { margin-top: 12px; }
    .confirm-dialog   { margin-top: 12px; padding: 0; }

    /* ===== 表单容器 ===== */
    div[data-testid="stForm"] {
        border: none !important;
        padding: 0 !important;
    }

    /* ===== 侧边栏统计卡片 ===== */
    .sidebar-stat {
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 8px;
    }
    .sidebar-stat .label {
        font-size: 11px;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 4px;
    }
    .sidebar-stat .value {
        font-size: 16px;
        font-weight: 700;
        color: #1e293b;
        font-variant-numeric: tabular-nums;
    }
    .sidebar-stat .value.positive { color: #059669; }
    .sidebar-stat .value.negative { color: #dc2626; }

    /* ===== 图表区域 ===== */
    .chart-section {
        background: #fff;
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 20px;
        box-shadow: var(--shadow-sm);
    }

    /* ===== 按钮微调 ===== */
    button[kind="secondary"] { font-size: 13px !important; }
    </style>
    """, unsafe_allow_html=True)


# ---- 数据库初始化 ------------------------------------------------------------

init_db()
_inject_css()

# ---- Session State 初始化 ---------------------------------------------------

DEFAULTS = {
    "add_type": "expense",
    "list_page": 1,
    "filter_hash": "",
    "pending_delete_id": None,
    "list_confirm_show": False,
}


def init_session_state() -> None:
    """初始化 session_state 默认值。"""
    for key, default in DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ---- 辅助函数 ---------------------------------------------------------------

def _all_categories() -> list[str]:
    """返回所有分类（支出 + 收入去重合并）。"""
    seen: set[str] = set()
    result: list[str] = []
    for cat in EXPENSE_CATEGORIES + INCOME_CATEGORIES:
        if cat not in seen:
            seen.add(cat)
            result.append(cat)
    return result


def _filter_hash(filters: dict) -> str:
    """计算筛选条件的 MD5 hash，用于检测筛选条件是否变化。"""
    raw = json.dumps(filters, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def _get_transaction_by_id(tid: int, transactions: list[dict]) -> dict | None:
    """从当前页交易列表中按 id 查找记录。"""
    for t in transactions:
        if t["id"] == tid:
            return t
    return None


# ---- 页面：添加记录 ---------------------------------------------------------

def _render_add_page() -> None:
    """渲染添加收支记录页面。"""
    st.markdown("## 📋 添加记录")
    st.caption("记录一笔新的收入或支出")

    # 类型切换
    type_ = st.radio(
        "类型",
        options=["expense", "income"],
        format_func=lambda t: "💸 支出" if t == "expense" else "💰 收入",
        horizontal=True,
        key="add_type",
    )
    categories = EXPENSE_CATEGORIES if type_ == "expense" else INCOME_CATEGORIES

    result = render_add_form(categories)
    if result is not None:
        add_transaction(
            type_=type_,
            amount=result["amount"],
            category=result["category"],
            date=result["date"],
            note=result["note"],
        )
        st.success(
            f"✅ 已添加 —— {result['date']}  "
            f"{'💰 收入' if type_ == 'income' else '💸 支出'}  "
            f"¥{result['amount']:,.2f}（{result['category']}）"
        )
        st.rerun()


# ---- 页面：查看列表 ---------------------------------------------------------

def _render_list_page() -> None:
    """渲染账目列表页面（含筛选、分页、删除）。"""
    st.markdown("## 📊 查看列表")
    st.caption("筛选、浏览和管理所有收支记录")

    years = get_years()

    filters = render_filter_bar(years, _all_categories(), show_type=True)

    # 筛选条件变化 → 重置页码
    new_hash = _filter_hash(filters)
    if st.session_state.filter_hash != new_hash:
        st.session_state.list_page = 1
        st.session_state.filter_hash = new_hash

    # 查询数据
    page = st.session_state.list_page
    records, total = query_transactions(
        year=filters["year"],
        month=filters["month"],
        category=filters["category"],
        type_=filters["type_"],
        page=page,
        page_size=PAGE_SIZE,
    )

    # 分页控件
    st.session_state.list_page = render_pagination(total, page, PAGE_SIZE, key_prefix="list")

    # 用最新页码重新查询
    records, total = query_transactions(
        year=filters["year"],
        month=filters["month"],
        category=filters["category"],
        type_=filters["type_"],
        page=st.session_state.list_page,
        page_size=PAGE_SIZE,
    )

    # 交易列表
    selected_ids, single_delete_id = render_transaction_list(records, key_prefix="list")

    # 分页控件（底部）
    if total > PAGE_SIZE:
        st.session_state.list_page = render_pagination(
            total, st.session_state.list_page, PAGE_SIZE, key_prefix="list_bottom"
        )

    # 单条删除
    if single_delete_id is not None:
        st.session_state.pending_delete_id = single_delete_id

    if st.session_state.pending_delete_id is not None:
        pending_txn = _get_transaction_by_id(
            st.session_state.pending_delete_id, records
        )
        if pending_txn is None:
            all_records, _ = query_transactions(page=1, page_size=9999)
            pending_txn = _get_transaction_by_id(
                st.session_state.pending_delete_id, all_records
            )

        action = render_single_delete_confirm(pending_txn, key_prefix="list")
        if action == "confirm":
            delete_transactions([st.session_state.pending_delete_id])
            st.session_state.pending_delete_id = None
            st.success("✅ 已删除")
            st.rerun()
        elif action == "cancel":
            st.session_state.pending_delete_id = None
            st.rerun()

    # 批量删除
    if selected_ids:
        confirmed = render_batch_delete_button(selected_ids, key_prefix="list")
        if confirmed:
            delete_transactions(list(selected_ids))
            st.session_state.list_confirm_show = False
            st.success(f"✅ 已删除 {len(selected_ids)} 条记录")
            st.rerun()


# ---- 页面：分类统计 ---------------------------------------------------------

def _render_stats_page() -> None:
    """渲染分类统计页面（柱状图 + 统计表）。"""
    st.markdown("## 📈 分类统计")
    st.caption("直观查看各分类的支出 / 收入占比")

    years = get_years()

    filters = render_filter_bar(years, _all_categories(), show_type=False)

    stats_type = st.radio(
        "查看类型",
        options=["expense", "income"],
        format_func=lambda t: "💸 支出统计" if t == "expense" else "💰 收入统计",
        horizontal=True,
        key="stats_type",
    )

    all_stats = get_category_stats(
        year=filters["year"],
        month=filters["month"],
    )
    filtered_stats = [s for s in all_stats if s["type"] == stats_type]

    if not filtered_stats:
        st.info("暂无统计数据，请先添加记录。")
        return

    st.markdown('<div class="chart-section">', unsafe_allow_html=True)

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("##### 📊 柱状图")
        render_bar_chart(filtered_stats)

    with col2:
        st.markdown("##### 📋 统计表")
        render_stats_table(filtered_stats)

    st.markdown('</div>', unsafe_allow_html=True)


# ---- 侧边栏 ----------------------------------------------------------------

def _render_sidebar(page: str) -> str:
    """渲染侧边栏导航与统计卡片。

    Returns:
        当前选中的页面标识。
    """
    with st.sidebar:
        st.markdown(
            '<h2 style="margin-bottom:4px;">💰 个人记账</h2>'
            '<p style="color:#9ca3af;font-size:12px;margin-top:0;">日常收支管理</p>',
            unsafe_allow_html=True,
        )
        st.divider()

        page = st.radio(
            "导航",
            options=["add", "list", "stats"],
            format_func=lambda p: {
                "add": "📋  添加记录",
                "list": "📊  查看列表",
                "stats": "📈  分类统计",
            }[p],
            label_visibility="collapsed",
        )

        st.divider()

        # 年度汇总统计卡片
        years = get_years()
        if years:
            curr_year = years[0]
            all_stats = get_category_stats(year=curr_year)
            expense_total = sum(s["total"] for s in all_stats if s["type"] == "expense")
            income_total = sum(s["total"] for s in all_stats if s["type"] == "income")
            balance = income_total - expense_total

            st.markdown(f'<p style="font-size:11px;color:#64748b;margin-bottom:8px;">📅 {curr_year} 年汇总</p>', unsafe_allow_html=True)

            st.markdown(
                f'<div class="sidebar-stat">'
                f'<div class="label">💰 收入</div>'
                f'<div class="value positive">¥{income_total:,.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="sidebar-stat">'
                f'<div class="label">💸 支出</div>'
                f'<div class="value negative">¥{expense_total:,.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            balance_class = "positive" if balance >= 0 else "negative"
            st.markdown(
                f'<div class="sidebar-stat">'
                f'<div class="label">📊 结余</div>'
                f'<div class="value {balance_class}">¥{balance:,.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    return page


# ---- 主函数 ----------------------------------------------------------------

def main() -> None:
    """应用主函数：侧边栏导航 + 页面路由。"""
    init_session_state()

    page = _render_sidebar("list")

    if page == "add":
        _render_add_page()
    elif page == "list":
        _render_list_page()
    else:
        _render_stats_page()


if __name__ == "__main__":
    main()
