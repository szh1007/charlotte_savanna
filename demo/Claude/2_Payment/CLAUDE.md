# CLAUDE.md — 2_Payment 记账工具

> 通用规范参见系统级 `~/.claude/CLAUDE.md` 和项目级 `CLAUDE.md`。

---

## 1. 项目概述

**个人记账工具**，基于 Streamlit + SQLite3，在浏览器中记录和管理日常收支。

核心功能：
- **添加记录**：类型（收入/支出）、金额、分类、日期、备注 → 表单提交
- **查看列表**：按年/月/分类/类型筛选 + 分页（10 条/页）+ 单条/批量删除
- **分类统计**：柱状图 + 占比统计表（含进度条可视化）

## 2. 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| Web 框架 | Streamlit 1.59 | 纯 Python，无需 HTML/JS |
| 数据库 | SQLite3 (`sqlite3`) | Python 标准库，零配置 |
| 图表 | `st.bar_chart` | Streamlit 内置，不引入第三方图表库 |
| 数据格式 | pandas DataFrame | Streamlit 硬依赖，自动安装 |
| 样式 | 内联 HTML/CSS + Streamlit 原生容器 | 自定义 CSS 变量、卡片布局、彩色标签 |

## 3. 目录结构

```
Claude/2_Payment/
├── CLAUDE.md           # 本文件
├── app.py              # 主入口：全局 CSS、页面路由、session_state、侧边栏
├── database.py         # 数据库层：连接管理、建表索引、CRUD、统计查询
├── constants.py        # 常量：10 个支出分类 + 6 个收入分类、PAGE_SIZE=10
├── ui_components.py    # UI 组件：表单、筛选栏、交易列表、分页、删除确认
├── charts.py           # 图表模块：柱状图、统计表（含进度条列）
├── schema.sql          # DDL 参考文件，可独立用 sqlite3 CLI 执行
├── requirements.txt    # 依赖：streamlit>=1.40.0
└── db.sqlite3          # 数据库文件（自动创建，已 gitignore）
```

### 模块职责与数据流

```
app.py（胶水层：路由 + session_state + CSS 注入）
├── _inject_css()           → 全局样式（CSS 变量、卡片、badge、分页、侧边栏）
├── _render_sidebar()       → 导航 radio + 年度汇总统计卡片
├── _render_add_page()      → 类型 radio + render_add_form() + add_transaction()
├── _render_list_page()     → 筛选 + 查询 + 列表 + 分页 + 单删/批删确认
└── _render_stats_page()    → 筛选 + 类型切换 + 柱状图 + 统计表
    │
    ├── database.py          ← 所有 SQL 操作（参数化查询，? 占位符）
    ├── ui_components.py     ← 纯 UI 渲染，不操作 session_state
    ├── charts.py            ← 纯图表渲染
    └── constants.py         ← 分类列表、PAGE_SIZE
```

## 4. 数据库

### 表：transactions

```sql
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT    NOT NULL CHECK(type IN ('income', 'expense')),
    amount      REAL    NOT NULL CHECK(amount > 0),
    category    TEXT    NOT NULL,
    date        TEXT    NOT NULL,           -- YYYY-MM-DD
    note        TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
```

- `type` — `income` / `expense`，`CHECK` 约束保证有效性
- `amount` — 始终为正数，`CHECK(amount > 0)`
- `date` — ISO 8601 文本，支持 SQLite `strftime()` 直接比较
- `created_at` — `datetime('now', 'localtime')`，记录创建时间
- 索引覆盖最常用的筛选字段 `date` 和 `category`

### database.py 函数清单

| 函数 | 返回 | 说明 |
|------|------|------|
| `get_connection()` | `sqlite3.Connection` | `check_same_thread=False`，`row_factory = Row` |
| `init_db()` | — | `CREATE IF NOT EXISTS`，幂等 |
| `add_transaction(type_, amount, category, date, note)` | `int` | 返回新记录 id |
| `query_transactions(year, month, category, type_, page, page_size)` | `(list[dict], int)` | 动态 WHERE + `LIMIT/OFFSET` + `COUNT(*)` |
| `delete_transactions(ids)` | `int` | `IN (?, ...)` 批量删除，返回影响行数 |
| `get_category_stats(year, month)` | `list[dict]` | 按 type 分组汇总，计算 percentage |
| `get_years()` | `list[int]` | `SELECT DISTINCT strftime('%Y', date)` 降序 |

## 5. UI 设计

### 5.1 全局样式

`_inject_css()` 在模块导入时注入自定义 CSS：
- **CSS 变量**：收入绿 `#059669`、支出红 `#dc2626`、边框色、圆角、阴影
- **卡片布局**：`st.container(border=True)` 用于筛选栏、表单、交易列表
- **空状态**：居中虚线框 + emoji
- **分页栏**：灰底圆角容器
- **侧边栏统计**：渐变色卡片（收入/支出/结余）
- **图表区域**：白底圆角卡片 + 微阴影

### 5.2 添加记录页

```
┌─ container(border=True) ─────────────────────┐
│  [类型 radio: 支出 | 收入]                     │
│  ┌─ 左栏 ────┐  ┌─ 右栏 ────┐                │
│  │ 金额      │  │ 日期      │                │
│  │ 分类      │  │ 备注      │                │
│  └───────────┘  └───────────┘                │
│  [ 添加记录 (primary button) ]                │
└──────────────────────────────────────────────┘
```

### 5.3 查看列表页

```
┌─ container(border=True) [筛选栏] ─────────────┐
│  年份 ▼  │  月份 ▼  │  分类 ▼  │  类型 ▼      │
└──────────────────────────────────────────────┘

┌─ pagination-bar ─────────────────────────────┐
│  [⏮首页] [◀上页]  第 1/3 页 · 共 25 条  [下页▶] [末页⏭] │
└──────────────────────────────────────────────┘

┌─ container(border=True) [交易列表] ───────────┐
│  表头: 日期 | 类型 | 分类 | 金额 | 备注 | 操作  │
│  ─────────────────────────────────────────── │
│  [☐] 07-01  💰收入  工资  ¥5,000.00  备注  [🗑️] │
│  ─────────────────────────────────────────── │
│  [☐] 07-02  💸支出  餐饮     ¥35.50  午餐  [🗑️] │
│  ...                                         │
└──────────────────────────────────────────────┘

┌─ batch-delete-bar ───────────────────────────┐
│  [🗑️ 批量删除（已选 N 条）]                    │
│  ┌─ confirm-dialog ──────────────────────┐   │
│  │  ⚠️ 确定删除？  [✅确认] [❌取消]       │   │
│  └───────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

交易列表行内样式：
- **类型**：圆角彩色标签（收入绿底绿字 / 支出红底红字）
- **金额**：右对齐，加粗，收入绿色 / 支出红色
- **备注**：超过 200px 省略号截断，空值显示灰色 "—"
- **行间**：`st.divider()` 分隔

### 5.4 分类统计页

```
┌─ 筛选栏 ──────────────────────────────────────┐
│  年份 ▼  │  月份 ▼                            │
└──────────────────────────────────────────────┘

[类型 radio: 💸 支出统计 | 💰 收入统计]

┌─ chart-section ──────────────────────────────┐
│  ┌─ 柱状图 ────┐  ┌─ 统计表 ────┐            │
│  │ st.bar_chart │  │ 分类 | 金额 | 占比 | 分布│            │
│  │              │  │ 餐饮 | ¥500 | 35% | █████│            │
│  │              │  │ 合计 | ¥... | 100%| ████│            │
│  └──────────────┘  └─────────────┘            │
└──────────────────────────────────────────────┘
```

统计表含进度条可视化（`█` 字符，每 5% 一个字符）。

## 6. Session State 管理

| key | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
| `add_type` | `str` | `"expense"` | 添加页当前选中的类型 |
| `list_page` | `int` | `1` | 列表页当前页码 |
| `filter_hash` | `str` | `""` | 筛选条件 MD5，变化时重置页码 |
| `pending_delete_id` | `int \| None` | `None` | 待确认删除的单条记录 id |
| `list_confirm_show` | `bool` | `False` | 批量删除确认弹窗是否可见 |

筛选条件变化检测：
```python
def _filter_hash(filters: dict) -> str:
    raw = json.dumps(filters, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()
```

## 7. 启动方式

```bash
cd Claude/2_Payment
pip install -r requirements.txt        # 首次安装 streamlit
streamlit run app.py                   # 启动 → http://localhost:8501
```

## 8. 开发约定

- **注释**：中文，解释"为什么"而非"是什么"
- **代码风格**：PEP 8，4 空格缩进，`snake_case`，类型注解（Python 3.10+ 语法）
- **SQL 安全**：全部 `?` 参数化查询，禁止字符串拼接；`IN (...)` 占位符数量由列表长度动态生成
- **Streamlit 特点**：
  - 每次交互重新执行整个脚本 → `init_db()` 必须幂等（`IF NOT EXISTS`）
  - 多线程环境 → `sqlite3.connect(check_same_thread=False)`
  - 脚本执行顺序：`st.set_page_config()` → `init_db()` → `_inject_css()` → `main()`
- **图表**：仅用 `st.bar_chart`，不引入 plotly/matplotlib
- **样式**：
  - 全局样式通过 `_inject_css()` 注入 `<style>` 块
  - 局部样式通过 `st.markdown(..., unsafe_allow_html=True)` 内联 `<span style="...">`
  - 卡片容器用 `st.container(border=True)`
  - 不使用跨 `st.markdown` 调用的 HTML div 包裹（Streamlit 每次渲染独立 HTML 块）
- **删除操作**：
  - 单条删除：`render_single_delete_confirm()` 返回三态字符串 `"confirm"` / `"cancel"` / `"none"`，由 `app.py` 处理状态清除
  - 批量删除：确认弹窗通过 `st.session_state.list_confirm_show` 控制显隐
  - 删除后调用 `st.rerun()` 刷新
- **分页**：每页 10 条（`PAGE_SIZE`），筛选条件变化时自动重置到第 1 页
- **ui_components.py 不操作 session_state**（除 `list_confirm_show` 标志外），状态管理集中在 `app.py`

---

> **创建日期**：2026-07-07 | **最后更新**：2026-07-07 | **维护者**：Claude Code (charlotte)
