"""repositories 的静态零件 (对齐各包的 utils/ 惯例: 行为在上层, 零件收在这里).

- `mapping`: 实体 ↔ 字典 / SQL 参数的转换 (打包与拆包)
"""

from __future__ import annotations

from CharAgent.db.repositories.utils.mapping import (
    entity_params,
    jsonable,
    model_to_dict,
)

__all__ = ["entity_params", "jsonable", "model_to_dict"]
