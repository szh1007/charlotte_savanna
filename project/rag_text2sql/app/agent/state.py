from typing import TypedDict


class DataAgentState(TypedDict):
    query: str
    error: str
    keywords: list
