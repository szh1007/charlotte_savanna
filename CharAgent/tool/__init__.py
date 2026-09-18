"""工具层 (tool 包): @tool 装饰器 + JSON schema 生成 + 执行包装.

设计依据 (CharAgent/docs):
- #2: 工具错误必须「可操作」—— 说清字段期望/实际, 供 agent loop 错误自纠错
- #10: 工具 JSON schema 质量直接决定模型填参正确率 (required/optional/enum/
  嵌套/约束), 生产用 pydantic 引擎 (签名 Annotated + Field, SOTA)
- #70: 工具粒度一个工具一件事, 参数越少越好; schema 自动生成保证与校验同源
- 用户决策: schema 生成 pydantic 为主 + manual 手写 typing 教学对照;
  文件结构按引擎拆子包 + utils 收编支撑 (拆分方案 B)

结构总览 (顶层 = 行为模块; 对齐 model 包「顶层行为 + utils 支撑」惯例):
- decorator.py      @tool 装饰器 + Tool 数据类 (name/description/schema/校验模型)
- executor.py       execute_tool 执行主流程: JSON 解析 → 校验 → 调用 → 规范化
- tools_demo.py     演示工具集: 5 pydantic SOTA + 1 manual 对照 (业务无关,
                    供 agent loop / CLI 复用; 业务工具属业务 demo 层)
- schema/           子包: 双引擎分开 —— engine_pydantic (SOTA 主路径) /
                    engine_manual (教学对照) + 共享支撑 signature (签名/docstring
                    内省) / types (产出类型)
- utils/            支撑子包: errors (异常语义) / messages (面向模型的可操作
                    文案生成, executor 拆出的独立纯函数)

模块内部 import 走具体模块路径 (utils/errors, schema.signature 等), 不绕
包门面, 避免隐式循环依赖; 对外公共 API 统一由本文件与 schema/__init__ 导出.
"""

from __future__ import annotations

from CharAgent.tool.decorator import Tool, tool
from CharAgent.tool.executor import ToolExecution, execute_tool
from CharAgent.tool.schema import (
    ToolSchemaInfo,
    build_manual_schema,
    build_pydantic_schema,
)
from CharAgent.tool.utils.errors import (
    ToolActionableError,
    ToolConfigError,
    ToolError,
)

__all__ = [
    "Tool",
    "ToolActionableError",
    "ToolConfigError",
    "ToolError",
    "ToolExecution",
    "ToolSchemaInfo",
    "build_manual_schema",
    "build_pydantic_schema",
    "execute_tool",
    "tool",
]
