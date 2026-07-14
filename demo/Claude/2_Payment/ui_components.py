"""可复用 UI 组件模块。

提供表单、筛选栏、分页控件、交易列表(含删除操作)、确认弹窗等组件。
所有组件只负责渲染和收集用户输入,不直接操作 session_state 或数据库。
"""

import streamlit as st

# ---- 添加记录表单 ------------------------------------------------------------


def render_add_form(categories: list[str]) -> dict | None:
    """渲染添加收支记录的表单。

    Args:
        categories: 当前类型对应的分类列表(由 app.py 根据 session_state 中的 type 选择传入)。

    Returns:
        提交成功时返回 {amount, category, date, note},未提交返回 None。
    """
    with st.container(border=True):
        st.markdown(
            '<p style="color:#6b7280;font-size:13px;margin-bottom:12px;">填写以下信息记录一笔新的收支</p>',
            unsafe_allow_html=True,
        )

        with st.form("add_transaction_form", clear_on_submit=True):
            col_left, col_right = st.columns([1, 1])

            with col_left:
                amount = st.number_input(
                    "金额",
                    min_value=0.01,
                    step=0.01,
                    format="%.2f",
                    help="请输入正数金额",
                )
                category = st.selectbox("分类", categories)

            with col_right:
                date = st.date_input("日期")
                note = st.text_area(
                    "备注",
                    placeholder="可选,简要说明这笔收支",
                    max_chars=200,
                    height=110,
                )

            submitted = st.form_submit_button(
                "添加记录",
                use_container_width=True,
                type="primary",
            )

            if submitted:
                return {
                    "amount": amount,
                    "category": category,
                    "date": date.strftime("%Y-%m-%d"),
                    "note": note.strip(),
                }
    return None


# ---- 筛选栏 ----------------------------------------------------------------


def render_filter_bar(
    years: list[int],
    categories: list[str],
    show_type: bool = True,
) -> dict:
    """渲染筛选栏:年份、月份、分类、类型(可选)。

    Args:
        years: 可选年份列表(降序)。
        categories: 所有分类列表(合并支出+收入,含"全部")。
        show_type: 是否显示类型筛选(列表页显示,统计页不显示)。

    Returns:
        筛选条件字典: {year, month, category, type_}。
        month/category/type_ 为 None 时表示"全部"。
    """
    with st.container(border=True):
        st.markdown(
            '<p style="color:#6b7280;font-size:13px;margin-bottom:8px;">🔍 筛选条件</p>',
            unsafe_allow_html=True,
        )

        cols = st.columns(4 if show_type else 3)

        with cols[0]:
            year_options = ["全部"] + [str(y) for y in years]
            year_str = st.selectbox(
                "年份",
                year_options,
                key="filter_year",
                label_visibility="collapsed",
            )
            year = int(year_str) if year_str != "全部" else None

        with cols[1]:
            month_options = ["全部"] + [f"{m:02d}月" for m in range(1, 13)]
            month_str = st.selectbox(
                "月份",
                month_options,
                key="filter_month",
                label_visibility="collapsed",
            )
            month = int(month_str.replace("月", "")) if month_str != "全部" else None

        with cols[2]:
            cat_options = ["全部", *categories]
            cat_str = st.selectbox(
                "分类",
                cat_options,
                key="filter_category",
                label_visibility="collapsed",
            )
            category = cat_str if cat_str != "全部" else None

        with cols[3] if show_type else cols[2]:
            if show_type:
                type_options = ["全部", "支出", "收入"]
                type_str = st.selectbox(
                    "类型",
                    type_options,
                    key="filter_type",
                    label_visibility="collapsed",
                )
                type_ = None if type_str == "全部" else "expense" if type_str == "支出" else "income"
            else:
                type_ = None

    return {"year": year, "month": month, "category": category, "type_": type_}


# ---- 交易列表(含删除操作)--------------------------------------------------

# 列比例常量:保证表头与数据行对齐一致
_TXN_COLS = [0.4, 1.4, 0.8, 0.8, 1.1, 2.3, 0.7]


def render_transaction_list(
    transactions: list[dict],
    key_prefix: str = "list",
) -> tuple[set[int], int | None]:
    """渲染交互式交易列表,每行含复选框和单条删除按钮。

    表头和数据行均使用相同比例的 st.columns,确保列对齐。
    外层用 st.container(border=True) 实现卡片样式。

    Args:
        transactions: 当前页的交易记录列表。
        key_prefix: Streamlit widget key 前缀。

    Returns:
        (selected_ids, single_delete_id)
    """
    if not transactions:
        st.markdown(
            '<div class="empty-state">'
            '<p style="font-size:48px;margin:0;">📭</p>'
            '<p style="color:#9ca3af;margin:8px 0 0 0;">暂无记录,请先添加数据</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return set(), None

    selected_ids: set[int] = set()
    single_delete_id: int | None = None

    with st.container(border=True):
        # ---- 表头 ----
        hcols = st.columns(_TXN_COLS)
        header_style = "font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;"
        with hcols[0]:
            st.markdown("")  # 复选框占位
        with hcols[1]:
            st.markdown(f'<span style="{header_style}">日期</span>', unsafe_allow_html=True)
        with hcols[2]:
            st.markdown(f'<span style="{header_style}">类型</span>', unsafe_allow_html=True)
        with hcols[3]:
            st.markdown(f'<span style="{header_style}">分类</span>', unsafe_allow_html=True)
        with hcols[4]:
            st.markdown(
                f'<span style="{header_style}text-align:right;display:block;">金额</span>',
                unsafe_allow_html=True,
            )
        with hcols[5]:
            st.markdown(f'<span style="{header_style}">备注</span>', unsafe_allow_html=True)
        with hcols[6]:
            st.markdown(f'<span style="{header_style}">操作</span>', unsafe_allow_html=True)

        st.divider()

        # ---- 数据行 ----
        for i, t in enumerate(transactions):
            tid = t["id"]
            cols = st.columns(_TXN_COLS)

            # 列 0:复选框
            with cols[0]:
                if st.checkbox(
                    "选",
                    key=f"{key_prefix}_cb_{tid}",
                    label_visibility="collapsed",
                ):
                    selected_ids.add(tid)

            # 列 1:日期
            with cols[1]:
                st.markdown(
                    f'<span style="font-size:13px;color:#374151;">{t["date"]}</span>',
                    unsafe_allow_html=True,
                )

            # 列 2:类型标签
            with cols[2]:
                if t["type"] == "income":
                    badge_style = "background:#ecfdf5;color:#059669;"
                    badge_text = "💰 收入"
                else:
                    badge_style = "background:#fef2f2;color:#dc2626;"
                    badge_text = "💸 支出"
                st.markdown(
                    f'<span style="display:inline-block;padding:2px 10px;'
                    f"border-radius:20px;font-size:12px;font-weight:500;"
                    f'{badge_style}">{badge_text}</span>',
                    unsafe_allow_html=True,
                )

            # 列 3:分类
            with cols[3]:
                st.markdown(
                    f'<span style="font-size:13px;color:#4b5563;">{t["category"]}</span>',
                    unsafe_allow_html=True,
                )

            # 列 4:金额(右对齐,红/绿色)
            with cols[4]:
                color = "#059669" if t["type"] == "income" else "#dc2626"
                st.markdown(
                    f'<div style="text-align:right;">'
                    f'<span style="font-weight:600;font-size:14px;color:{color};">'
                    f"¥{t['amount']:,.2f}</span></div>",
                    unsafe_allow_html=True,
                )

            # 列 5:备注
            with cols[5]:
                note = t["note"] or '<span style="color:#d1d5db;">—</span>'
                st.markdown(
                    f'<span style="font-size:12px;color:#6b7280;'
                    f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                    f'max-width:200px;display:inline-block;">{note}</span>',
                    unsafe_allow_html=True,
                )

            # 列 6:删除按钮
            with cols[6]:
                if st.button(
                    "🗑️",
                    key=f"{key_prefix}_del_{tid}",
                    help="删除此条记录",
                ):
                    single_delete_id = tid

            # 行间分隔线(最后一行不加)
            if i < len(transactions) - 1:
                st.divider()

    return selected_ids, single_delete_id


# ---- 分页控件 --------------------------------------------------------------


def render_pagination(
    total: int,
    current_page: int,
    page_size: int = 10,
    key_prefix: str = "list",
) -> int:
    """渲染分页控件。

    Args:
        total: 总记录数。
        current_page: 当前页码(1-based)。
        page_size: 每页条数。
        key_prefix: 键前缀。

    Returns:
        用户选择的目标页码。
    """
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = max(1, min(current_page, total_pages))

    new_page = current_page

    st.markdown('<div class="pagination-bar">', unsafe_allow_html=True)

    p_cols = st.columns([0.7, 0.7, 2.0, 0.7, 0.7])

    with p_cols[0]:
        if st.button("⏮ 首页", disabled=(current_page <= 1), key=f"{key_prefix}_pg_first"):
            new_page = 1

    with p_cols[1]:
        if st.button("◀ 上页", disabled=(current_page <= 1), key=f"{key_prefix}_pg_prev"):
            new_page = current_page - 1

    with p_cols[2]:
        st.markdown(
            f'<div class="pagination-info">'
            f"第 <b>{current_page}</b> / <b>{total_pages}</b> 页"
            f'<span class="pagination-total"> · 共 {total} 条记录</span>'
            f"</div>",
            unsafe_allow_html=True,
        )

    with p_cols[3]:
        if st.button("下页 ▶", disabled=(current_page >= total_pages), key=f"{key_prefix}_pg_next"):
            new_page = current_page + 1

    with p_cols[4]:
        if st.button("末页 ⏭", disabled=(current_page >= total_pages), key=f"{key_prefix}_pg_last"):
            new_page = total_pages

    st.markdown("</div>", unsafe_allow_html=True)

    return new_page


# ---- 批量删除按钮 -----------------------------------------------------------


def render_batch_delete_button(
    selected_ids: set[int],
    key_prefix: str = "list",
) -> bool:
    """渲染批量删除按钮及确认弹窗。

    Args:
        selected_ids: 当前选中的记录 id 集合。
        key_prefix: 键前缀。

    Returns:
        True 表示用户确认了批量删除操作。
    """
    if not selected_ids:
        return False

    st.markdown('<div class="batch-delete-bar">', unsafe_allow_html=True)

    col1, _col2 = st.columns([1.5, 4.5])
    with col1:
        if st.button(
            f"🗑️ 批量删除(已选 {len(selected_ids)} 条)",
            type="secondary",
            key=f"{key_prefix}_batch_del",
            use_container_width=True,
        ):
            st.session_state[f"{key_prefix}_confirm_show"] = True

    # 确认弹窗
    if st.session_state.get(f"{key_prefix}_confirm_show", False):
        st.markdown('<div class="confirm-dialog">', unsafe_allow_html=True)
        st.warning(f"确定要删除选中的 {len(selected_ids)} 条记录吗?此操作不可撤销。")
        cc1, cc2, _cc3 = st.columns([1, 1, 4])
        with cc1:
            if st.button(
                "✅ 确认删除",
                key=f"{key_prefix}_confirm_yes",
                use_container_width=True,
            ):
                st.session_state[f"{key_prefix}_confirm_show"] = False
                st.markdown("</div>", unsafe_allow_html=True)
                return True
        with cc2:
            if st.button(
                "❌ 取消",
                key=f"{key_prefix}_confirm_no",
                use_container_width=True,
            ):
                st.session_state[f"{key_prefix}_confirm_show"] = False
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    return False


# ---- 单条删除确认 -----------------------------------------------------------


def render_single_delete_confirm(
    transaction: dict | None,
    key_prefix: str = "list",
) -> str:
    """渲染单条删除确认弹窗。

    不直接操作 session_state,由调用方(app.py)根据返回值决定行为。

    Args:
        transaction: 待删除的交易记录 dict,None 表示无待确认操作。
        key_prefix: 键前缀。

    Returns:
        "confirm" — 用户点击了确认删除。
        "cancel"  — 用户点击了取消。
        "none"    — 无待确认操作或用户尚未做出选择。
    """
    if transaction is None:
        return "none"

    st.markdown('<div class="confirm-dialog">', unsafe_allow_html=True)

    type_label = "💰 收入" if transaction["type"] == "income" else "💸 支出"
    st.warning(
        f"确定要删除此记录吗?—— {transaction['date']}  {type_label}  "
        f"¥{transaction['amount']:,.2f}({transaction['category']})"
    )

    cc1, cc2, _cc3 = st.columns([1, 1, 4])
    with cc1:
        if st.button(
            "✅ 确认删除",
            key=f"{key_prefix}_single_yes",
            use_container_width=True,
        ):
            st.markdown("</div>", unsafe_allow_html=True)
            return "confirm"
    with cc2:
        if st.button(
            "❌ 取消",
            key=f"{key_prefix}_single_no",
            use_container_width=True,
        ):
            st.markdown("</div>", unsafe_allow_html=True)
            return "cancel"
    st.markdown("</div>", unsafe_allow_html=True)
    return "none"
