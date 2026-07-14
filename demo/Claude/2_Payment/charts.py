"""图表模块:分类柱状图与统计表渲染。

仅依赖 Streamlit 内置图表 API(st.bar_chart),不引入 plotly/matplotlib。
"""

import pandas as pd
import streamlit as st


def render_bar_chart(stats: list[dict]) -> None:
    """绘制分类金额柱状图。

    Args:
        stats: get_category_stats() 的返回值,
               每项为 {type, category, total, percentage}。
    """
    if not stats:
        st.info("暂无统计数据。")
        return

    df = pd.DataFrame(stats)
    chart_data = df.set_index("category")["total"].sort_values(ascending=True)

    st.bar_chart(
        chart_data,
        use_container_width=True,
        x_label="分类",
        y_label="金额(元)",
        height=380,
    )


def render_stats_table(stats: list[dict]) -> None:
    """渲染分类统计表格,含金额和占比列,带进度条可视化。

    Args:
        stats: get_category_stats() 的返回值。
    """
    if not stats:
        st.info("暂无统计数据。")
        return

    # 按金额降序排列
    sorted_stats = sorted(stats, key=lambda s: s["total"], reverse=True)
    total_amount = sum(s["total"] for s in sorted_stats)

    # 构建展示用的 DataFrame
    rows = []
    for item in sorted_stats:
        pct = item["percentage"]
        # 进度条(用 █ 字符模拟)
        bar_width = max(1, int(pct / 5))  # 每 5% 一个字符
        bar = "█" * bar_width
        rows.append(
            {
                "分类": item["category"],
                "金额": f"¥{item['total']:,.2f}",
                "占比": f"{pct}%",
                "分布": bar,
            }
        )

    # 添加总计行
    rows.append(
        {
            "分类": "合计",
            "金额": f"¥{total_amount:,.2f}",
            "占比": "100%",
            "分布": "█" * 20,
        }
    )

    display_df = pd.DataFrame(rows)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "分类": st.column_config.TextColumn("分类", width="small"),
            "金额": st.column_config.TextColumn("金额", width="small"),
            "占比": st.column_config.TextColumn("占比", width="small"),
            "分布": st.column_config.TextColumn("", width="medium"),
        },
        height=(len(rows) * 37 + 38),
    )
