"""schema 生成子包: pydantic(生产 SOTA) + manual(手写 typing 对照) 双引擎.

按引擎拆文件 (教学对照清晰, 两引擎实现思路截然不同); 共享支撑独立模块.
对外 import 面与拆分前一致 (CharAgent.tool.schema.build_pydantic_schema 等).

模块分工:
- types.py            共用数据结构: Json 别名 / ToolSchemaInfo (schema + 校验模型)
- signature.py        函数签名内省: 参数条目 / Annotated 拆解 / docstring 描述
                      (parse_google_args_doc / first_paragraph, decorator 共用)
- engine_pydantic.py  主引擎 (SOTA): 签名 → create_model → model_json_schema,
                      含 $defs 内联 / title 清理 / FieldInfo 归并等 pydantic
                      产物专属后处理
- engine_manual.py    对照引擎: 手写 typing → JSON schema 递归映射 (零依赖)

描述与形状的来源约定 (两引擎统一, 防魔法解读, 定位问题方便):
- 参数形状 (名称/类型/默认值/约束): 只从函数签名读取, docstring 不承载设置
- Field 两种等价写法 (pydantic 引擎, 类字段语义, 二选一不可同用):
    x: Annotated[str, Field(...)]          # 注解位 (推荐)
    x: str = Field(...)                    # 默认值位 (引擎归一化后同路径)
- 参数 description: pydantic 引擎取 Field(description), 缺省时 docstring Args
  兜底; manual 引擎唯一取自 docstring Args —— Annotated 内任意字符串 metadata
  不解释为描述 (PEP 593 未定义该语义)
- 工具 description: docstring 首段 (decorator.py 提取)
"""

from __future__ import annotations

from CharAgent.tool.schema.engine_manual import build_manual_schema
from CharAgent.tool.schema.engine_pydantic import build_pydantic_schema
from CharAgent.tool.schema.signature import (
    first_paragraph,
    parse_google_args_doc,
)
from CharAgent.tool.schema.types import Json, ToolSchemaInfo

__all__ = [
    "Json",
    "ToolSchemaInfo",
    "build_manual_schema",
    "build_pydantic_schema",
    "first_paragraph",
    "parse_google_args_doc",
]
