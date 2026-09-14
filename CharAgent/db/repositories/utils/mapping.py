"""实体 ←→ 字典 / 参数 的转换零件 (仓储共用).

一句话理解: 这是**打包与拆包**的地方. 实体对象 (Python 里的一个类) 要变成 SQL
语句的参数 (一个字典), 数据库读回来的一行也要变回实体对象 —— 这两下转换在四个
仓储里重复出现, 收在这里各写一份.

三件事:
- `model_to_dict`: 实体 → 可 JSON 序列化的快照 (测试比对、日志、P1 的接口返回)
- `entity_params`: 实体 → SQL 参数 (写入用; 只管「有哪些列、值是什么」)
- `jsonable`: 把 datetime 之类转成 JSON 能装的形态

**这里不做校验, 也不做业务判断** —— 值合不合法由构造实体的那一方 (state.py /
conversation.py / 上层调用者) 负责, 这里只管搬运. 搬运工乱加判断的结果是同一个
规则散落两处, 迟早不一致.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase


def model_to_dict(entity: DeclarativeBase) -> dict[str, Any]:
    """实体 → 字段字典 (键与数据库列名一致, 值已转成 JSON 能装的形态).

    为什么要有它: 测试里想断言「存进去的就是我造的那个」, 得先把实体摊平成可比
    较的形态; 日志与 P1 的接口返回也需要一份不含 SQLAlchemy 内部状态的口径.
    直接 `entity.__dict__` 拿到的是带下划线前缀的 ORM 内部字段, 不能用.

    Args:
        entity: 任意映射过的实体 (Thread / Run / Message / ToolCall / CheckpointRow).

    Returns:
        dict[str, Any]: 列名 → 值; 顺序与建表时的列顺序一致.
    """
    mapper = inspect(entity).mapper
    return {
        column.key: jsonable(getattr(entity, column.key)) for column in mapper.columns
    }


def entity_params(entity: DeclarativeBase) -> dict[str, Any]:
    """实体 → SQL 参数 (写入语句里的参数名与列名一致).

    与 `model_to_dict` 的区别: 这个**不转换值** —— SQLAlchemy 知道怎么把
    datetime / dict / Decimal 交给驱动, 提前转成文本反而会让时间列收到字符串
    (Postgres 能接受, 但时区与类型就模糊了).

    用法 (仓储里的写法)::

        session.execute(table.insert().values(**entity_params(thread)))
    """
    mapper = inspect(entity).mapper
    return {column.key: getattr(entity, column.key) for column in mapper.columns}


def jsonable(value: Any) -> Any:
    """把值转成 JSON 能装的形态 (datetime → ISO 文本).

    为什么不用 `json.dumps(..., default=str)`: 那是**兜底** —— 只有 dumps 自己
    认不出来的时候才轮到它, 于是「什么时候会被转成字符串」是不可预测的, 某天
    就会冒出一个 `"<object at 0x...>"` 这种谁也读不懂的东西. 这里显式列出认得
    的类型, 其余原样返回.

    - datetime / date → ISO 8601 字符串 (`2026-09-14T10:00:00+00:00`)
    - 其余 (str / int / float / bool / None / list / dict / Decimal) 原样返回:
      它们本来就装得进 JSON
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
