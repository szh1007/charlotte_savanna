from typing import TypedDict


class ColumnInfoQdrant(TypedDict):
    id: str
    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list
    table_id: str


class MetricInfoQdrant(TypedDict):
    id: str
    name: str
    description: str
    relevant_columns: list
    alias: list
