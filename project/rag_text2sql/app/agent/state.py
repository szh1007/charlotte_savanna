from typing import TypedDict

from app.models.es import ValueInfoEs
from app.models.qdrant import ColumnInfoQdrant, MetricInfoQdrant


class DataAgentState(TypedDict):
    query: str
    error: str
    keywords: list
    retrieved_columns: list[ColumnInfoQdrant]
    retrieved_metrics: list[MetricInfoQdrant]
    retrieved_values: list[ValueInfoEs]
