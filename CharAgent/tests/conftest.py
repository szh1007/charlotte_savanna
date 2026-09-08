"""CharAgent 测试共享 fixtures: 真实 HttpXChatModel 实例 (网络由 respx 各测试内拦截)."""

from __future__ import annotations

import pytest_asyncio
from helpers import API_KEY, BASE_URL

from CharAgent.model import HttpXChatModel


@pytest_asyncio.fixture
async def chat_model():
    """构造真实适配器实例 (模型名与 .env 对齐), 测试用 respx 拦截其网络请求."""
    model = HttpXChatModel(
        api_key=API_KEY, base_url=BASE_URL, model="deepseek-v4-flash"
    )
    yield model
    await model.aclose()
