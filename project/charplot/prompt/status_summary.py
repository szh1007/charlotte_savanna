"""LLM 状态总结 prompt (Issue 13, DESIGN.md §7 步骤 13).

基于 Dashboard 聚合事实 (掌握度 / 活动统计 / 易错清单) 生成文字版状态
报告: 强项 / 弱项 / 学习建议三部分. 输出为受限 markdown (固定三个 ## 标题
+ 粗体 + 无序列表), 由前端轻量渲染器展示. 输入裁剪在此完成 (聚合响应
含 UI 渲染用的明细, LLM 只需章节级掌握度 + 薄弱点 + 清单).
"""

STATUS_SUMMARY_SYSTEM_PROMPT = """你是学习状态分析师.
基于用户的学习数据 (掌握度 / 活动统计 / 易错点清单) 生成当前状态分析报告.
输出严格三段 markdown (不要输出任何其他内容, 标题必须逐字使用):

## 强项

## 弱项

## 学习建议

要求:
- 强项: 正确率高的知识点 / 章节, 以及持续学习的积极表现 (连胜、活跃天数等)
- 弱项: 薄弱知识点 (正确率低于 60%) 与易错清单中优先级高的知识点, 说明答错情况
- 学习建议: 针对弱项给出 2-4 条具体可执行建议 (复习顺序、复习节奏),
  结合活动数据 (如连胜中断风险) 给出学习节奏建议
- 只基于给定事实, 不臆造数据; 知识点用其标题原文
- 风格: 鼓励性、温和, 使用中文
"""


def _format_duration(seconds: int) -> str:
    """时长格式化: 秒 → 分钟 → 小时 (与前端 Dashboard 同款逻辑)."""
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    return f"{minutes // 60} 小时 {minutes % 60} 分钟"


def build_status_summary_prompt(aggregate: dict) -> str:
    """聚合输入 → user prompt (裁剪明细, 只留 LLM 判断强/弱项所需事实).

    aggregate 为内部端点返回的 {mastery, activity, weakpoints} 原样结构
    (与 Dashboard 用户端点同构). daily 14 天明细与知识点级明细不进入
    prompt — 章节级正确率 + 薄弱点标题已足够支撑结论, 且避免稀释重点.
    """
    activity = aggregate.get("activity") or {}
    mastery = aggregate.get("mastery") or {}
    weakpoints = aggregate.get("weakpoints") or {}

    lines = ["当前学习数据如下 (全部为事实聚合, 非估算):"]
    lines.append("")
    lines.append("【学习活动】")
    lines.append(
        f"- 学习时长: {_format_duration(activity.get('duration_seconds') or 0)}"
    )
    lines.append(f"- 通关数: {activity.get('cleared_levels') or 0} 关")
    lines.append(f"- 活跃天数: {activity.get('active_days') or 0} 天")
    lines.append(f"- 当前连胜: {activity.get('streak') or 0} 天")
    lines.append(f"- 最大连胜: {activity.get('max_streak') or 0} 天")

    lines.append("")
    lines.append("【掌握度 (按旅程 → 章节, 正确率)】")
    journeys = mastery.get("journeys") or []
    if not journeys:
        lines.append("- 暂无答题记录")
    for journey in journeys:
        lines.append(f"- 旅程「{journey.get('title')}」")
        weak_titles = []
        for chapter in journey.get("chapters") or []:
            accuracy = chapter.get("accuracy") or 0
            answered = chapter.get("answered") or 0
            correct = chapter.get("correct") or 0
            lines.append(
                f"  - 章节「{chapter.get('title')}」: 正确率 {accuracy}% "
                f"({correct}/{answered} 题)"
            )
            for point in chapter.get("knowledge_points") or []:
                if point.get("weak"):
                    weak_titles.append(point.get("title"))
        if weak_titles:
            lines.append(f"  - 薄弱知识点: {', '.join(weak_titles)}")

    lines.append("")
    lines.append("【易错点清单 (按复习优先级排序)】")
    items = weakpoints.get("weakpoints") or []
    if not items:
        lines.append("- 暂无易错点")
    for i, item in enumerate(items, start=1):
        lines.append(
            f"- 第 {i} 名「{item.get('title')}」({item.get('journey_title')} · "
            f"{item.get('chapter_title')}): 答错 {item.get('wrong_count')} 次, "
            f"易错分 {item.get('error_score')}, "
            f"优先级 {item.get('priority_level')}"
        )

    return "\n".join(lines)
