"""schema 引擎共用数据结构: Json 别名与 ToolSchemaInfo 产出类型."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

type Json = dict[str, Any]


@dataclass(slots=True)
class ToolSchemaInfo:
    """schema 引擎产出: wire 参数 schema + 校验模型 + 调用形态.

    - parameter_model: pydantic 参数模型 (executor 校验 arguments 用),
      manual 引擎为 None (无自动校验)
    - single_param_name: 唯一 BaseModel 参数形态的参数名 (executor 以模型实例
      作为该位置实参调用); 常规 kwargs 形态为 None
    """

    parameters: Json
    parameter_model: type[BaseModel] | None
    single_param_name: str | None
