# 03-P0-2 — @tool 装饰器 + JSON schema 生成

**What to build:** @tool 装饰器将 Python 函数注册为可被模型调用的 Tool（名称、描述、JSON schema 自动生成，参数设计遵循"一个工具一件事、参数越少越好"）。工具执行失败的错误语义为**可操作错误**（#2：说明字段格式期望，如"order_no 应为 14 位数字"，而非甩 422），为 loop 的错误自纠错提供基础。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] @tool 装饰器完成：函数 → Tool（name/description/parameters JSON schema）
- [ ] schema 生成正确性测试：复杂参数类型（嵌套 dict/list/enum/可选参数）映射准确（#70）
- [ ] 可操作错误语义：工具抛错 → 结构化的可操作错误信息（#2）
- [ ] 执行包装：参数校验失败给出明确字段期望而非原始 traceback
