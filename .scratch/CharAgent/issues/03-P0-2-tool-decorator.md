# 03-P0-2 — @tool 装饰器 + JSON schema 生成

**What to build:** @tool 装饰器将 Python 函数注册为可被模型调用的 Tool（名称、描述、JSON schema 自动生成，参数设计遵循"一个工具一件事、参数越少越好"）。工具执行失败的错误语义为**可操作错误**（#2：说明字段格式期望，如"order_no 应为 14 位数字"，而非甩 422），为 loop 的错误自纠错提供基础。

**Blocked by:** None — can start immediately

**Status:** done

- [x] @tool 装饰器完成：函数 → Tool（name/description/parameters JSON schema）
- [x] schema 生成正确性测试：复杂参数类型（嵌套 dict/list/enum/可选参数）映射准确（#70）
- [x] 可操作错误语义：工具抛错 → 结构化的可操作错误信息（#2）
- [x] 执行包装：参数校验失败给出明确字段期望而非原始 traceback

## Comments

**2026-09-09 实施完成**（提交前 code-review 双轴审查 + 修复）：实现位于 `CharAgent/tool/`（errors / schema / decorator / executor / demo_tools）。

- **用户决策**：工具定义按生产 SOTA 写法（签名级 `Annotated + pydantic Field`，OpenAI function calling / Pydantic AI 同款）；schema 生成 pydantic 为主路径 + `@tool(schema="manual")` 手写 typing 映射教学对照（零 pydantic 依赖，证明「注解 → JSON schema」原理）。
- **双引擎**：pydantic 引擎 `create_model` 动态聚合签名参数 → `model_json_schema()`（$defs/$ref 递归展开内联 + 字段级 description/examples 兄弟键并回且优先、全层去 title、additionalProperties False）；manual 引擎 inspect + typing 手写递归映射（str/int/float/bool/list/dict/Literal/Enum/单类型|None → anyOf）。同一纯注解 + Google docstring 签名喂两引擎产出**等值** schema，契约测试约束。
- **错误语义（#2）**：pydantic ValidationError 逐条映射为「字段路径 + 期望 + 实际」中文可操作文本（缺必填 / 类型 hint 表 / enum 允许值展开 / pattern 原文 / gt/ge/lt/le 界值入文案且措辞区分「必须大于 vs 不能小于」）；manual 引擎由 executor 形状检查兜底缺必填 / 未知参数（附可用参数列表）+ 工具函数内 raise `ToolActionableError` 承担业务规则校验（#5 vs #6 对照演示）；意外异常回填通用内部文案（traceback 不外泄，根因保留 `ToolExecution.exception` 供日志）。
- **演示工具集**（demo_tools，业务无关、无外部依赖，供 P0-3 loop / P0-10 CLI 复用）：get_current_time / convert_length / batch_convert_lengths（list[嵌套模型]）/ count_text_stats / query_order_status（Field pattern + mock）/ query_order_status_manual（同能力 manual 对照，同输入同输出）。
- 验收证据：新增 53 个 tool 用例（schema 双引擎 / executor 错误语义 / demo 冒烟 / decorator），全量 148 passed + 6 skipped（integration 门控）；Ruff check + format 零告警。
- **code-review 修复**（双轴审查后）：manual 工具缺参/未知参数原回填误导性「内部错误」→ 形状检查可操作化；gt/lt 界值与措辞补齐（原仅 ge/le 且措辞错位）；pattern 文案去除 repr 转义；docstring Args 解析对无空行分隔的 Returns 章节头截断（原会并入参数描述）；duration_ms 单一出口（try/finally）+ 术语表 ToolResult（CONTEXT.md:31）与 ToolExecution 的桥接注释。
