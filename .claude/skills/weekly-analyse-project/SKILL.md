---
name: weekly-analyse-project
description: 分析项目近一周的 git 提交、代码变更、工作趋势和健康度，生成结构化的周报 Markdown 文件。当用户提到"周报"、"weekly report"、"项目进展"、"本周工作总结"、"分析项目"、"进度报告"、"weekly analysis"时使用此 skill。即使只是提到"最近做了什么"或"项目进展如何"，也应该主动触发此 skill。
---

# 项目周报分析

对项目近一周的开发活动进行全面分析，生成结构化周报。

## 报告结构

ALWAYS 使用以下模板生成报告：

```
# [项目名] 周报 — YYYY-MM-DD

## 1. 概览

- 报告周期
- 总提交数 / 活跃天数 / 贡献者
- 与上周对比的活跃度变化

## 2. 提交分类汇总

按 Conventional Commits 类型分组（feat / fix / docs / refactor / chore / test / style / 其他），每组列出：
- 提交数量
- 关键提交的简要说明

## 3. 代码变更统计

- 新增行数 / 删除行数 / 净增行数
- 变更文件数（新增 / 修改 / 重命名 / 删除）
- 高频变更 Top 5 文件及变更次数

## 4. 与前一周对比

| 指标 | 本周 | 上周 | 变化 |
|------|------|------|------|
| 提交数 | N | N | ±N/% |
| 新增行数 | N | N | ±N/% |
| 删除行数 | N | N | ±N/% |
| 贡献者数 | N | N | ±N |

## 5. 项目健康度

- 是否有 TODO/FIXME/HACK 标记的新增（与上周对比数量）
- 是否有未跟踪文件（`git ls-files --others --exclude-standard`）
- 是否有未提交的本地变更
- 分支状态（是否落后/超前于 remote）

## 6. 本周亮点

- 最重要的 1-3 项成果
- 值得关注的变化或风险

## 7. 下周展望

（如果可以从提交趋势、TODO 标记推断，给出简短建议；否则标注"待用户补充"）
```

## 分析步骤

### Step 1: 确定时间范围

- 近一周：从今天往前推 7 天。
- 计算方式：`git log --since="7 days ago" --format="%H"` 确保覆盖准确。
- 上一周对比期：8-14 天前。

### Step 2: 收集 Git 数据

全部通过 `git` 命令获取，不依赖第三方工具。

**提交列表与分类：**
```bash
git log --since="7 days ago" --format="%h %s (%ai)" --reverse
git log --since="7 days ago" --format="%h %an %s"  # 按作者
```

**代码量统计：**
```bash
git log --since="7 days ago" --shortstat | grep -E "fil(e|es) changed" | awk '{files+=$1; inserted+=$4; deleted+=$6} END {print files, inserted, deleted}'
```

**高频变更文件：**
```bash
git log --since="7 days ago" --name-only --format="" | sort | uniq -c | sort -rn | head -5
```

**上周对比数据（8-14 天前）：**
```bash
git log --since="14 days ago" --until="7 days ago" --oneline | wc -l
git log --since="14 days ago" --until="7 days ago" --shortstat | grep -E "fil(e|es) changed" | awk '{inserted+=$4; deleted+=$6} END {print inserted, deleted}'
```

### Step 3: 项目健康度检查

```bash
# TODO/FIXME/HACK 数量变化
git diff HEAD~7 -- '*.py' '*.js' '*.ts' '*.html' | grep -cE 'TODO|FIXME|HACK'   # 本周新增
git diff HEAD~14..HEAD~7 -- '*.py' '*.js' '*.ts' '*.html' | grep -cE 'TODO|FIXME|HACK'  # 上周新增（对比）

# 未跟踪文件
git ls-files --others --exclude-standard

# 未提交变更
git status --porcelain

# 分支状态
git status -b --porcelain
```

### Step 4: 活跃度评估

- **活跃天数**：有提交的天数（从 `git log` 日期中提取唯一值）。
- **提交分布**：按天统计提交数，判断是否持续产出（定期有代码产出 = 至少 4 天有提交记为"活跃"）。
- **产出节奏**：持续高强度（每天 ≥2 提交）、稳定产出（≥4 天有提交）、低频（2-3 天有提交）、停滞（≤1 天有提交）。

### Step 5: 生成报告

1. 按模板填充所有数据。
2. 对每个 feat/fix 提交写一行简短说明（中文）。
3. 对比数据填入表格，计算变化百分比。
4. 将报告保存为 `docs/skills_md/weekly-analyse-project/YYYY-MM-DD.md`（使用当前日期）。
5. 在终端输出报告摘要（概览 + 亮点），完整内容请用户查看文件。

### Step 6: 展示原则

- 终端先输出简版（概览表 + 亮点 2-3 条），完整报告指向保存的文件路径。
- 数据优先：能用表格就用表格，减少冗长段落。
- 对比给结论：不只列数字，加简短判断（"本周提交量翻倍"、"活跃度与上周持平"）。
