"""LLM 调用的预算与瞬态重试 (C16).

现场由来 (C15 跑批, 117 次真跑): 2 次卡在 `recall_value` 的 LLM 调用上, 请求一直
飞, 240s 被跑批器兜底掐掉 —— 因为客户端读超时是 SDK 默认的 600s, 等于没有预算.
本票显式给了 `timeout` + `max_retries` (判据交给 OpenAI SDK, 它只重瞬态), 这份用例
用一个**本地假上游**把三件事钉住:

1. 5xx 是瞬态: 重试后成功 (票据验收「LLM 返回 502 时整图不再直接失败」)
2. 4xx 不是瞬态: 直接放弃, 只打一次
3. 上游挂起 (收下请求但不回): 在预算内以超时异常结束, 且**重试也照做**
   —— 挂起属于瞬态, 这正是 C15 那两次的形状

再钉一条「配置真的传到了客户端」: 免得哪天参数改名, 预算静默失效.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import openai
import pytest
from langchain_deepseek import ChatDeepSeek

COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _llm(
    transport: httpx.AsyncBaseTransport, *, timeout: float = 1.0, max_retries: int = 2
):
    return ChatDeepSeek(
        model="test-model",
        api_key="test-key",
        base_url="https://fake-upstream.local/v1",
        http_async_client=httpx.AsyncClient(transport=transport),
        timeout=timeout,
        max_retries=max_retries,
    )


async def test_transient_502_is_retried_then_succeeds() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) < 3:
            return httpx.Response(502, json={"error": {"message": "bad gateway"}})
        return httpx.Response(200, json=COMPLETION)

    llm = _llm(httpx.MockTransport(handler))

    reply = await llm.ainvoke("你好")

    assert reply.content == "ok"
    assert len(attempts) == 3, "两次 502 之后第三次成功 —— 整图不该被一次 502 打断"


async def test_4xx_is_not_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    llm = _llm(httpx.MockTransport(handler))

    with pytest.raises(openai.BadRequestError):
        await llm.ainvoke("你好")

    assert len(attempts) == 1, "参数错重试一百次也一样 —— 4xx 直接放弃"


async def test_hanging_upstream_ends_within_budget() -> None:
    """复现 C15 的现场形状: 请求被接受, 但上游一直不回.

    度量只包住 `ainvoke` —— 收尾时 `wait_closed()` 会等 handler 收尾
    (Python 3.12 起的行为), 把它算进来会量出假的长耗时 (这里踩过一次:
    服务端睡 30s, 断言读到 30.7s, 看着像"超时没生效", 其实超时 1s 就抛了).
    """
    connections: list[int] = []
    handlers: list[asyncio.Task] = []

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connections.append(1)
        handlers.append(asyncio.current_task())
        # 先把请求收下 (不读的话 httpx 的读超时不会启动 —— 实测), 再一直不回
        try:
            await reader.read(65536)
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    llm = ChatDeepSeek(
        model="test-model",
        api_key="test-key",
        base_url=f"http://127.0.0.1:{port}/v1",
        timeout=0.3,
        max_retries=1,
    )

    started = time.perf_counter()
    with pytest.raises(openai.APITimeoutError):
        await llm.ainvoke("你好")
    elapsed = time.perf_counter() - started

    server.close()
    for task in handlers:
        task.cancel()
    await asyncio.gather(*handlers, return_exceptions=True)
    await server.wait_closed()

    assert elapsed < 5, f"超时预算没生效, 等了 {elapsed:.1f}s"
    assert len(connections) == 2, "挂起属于瞬态 —— 第一次超时后要再试一次"


def test_timeout_and_retries_reach_the_sdk_client() -> None:
    """钉的是**机制**: 这两个参数真会落到 SDK 客户端上.

    只断言 `llm.request_timeout == app_config.llm.timeout_s` 是不够的 ——
    配置替身在场时两边都是空值, 断言会空转通过 (评审指出); 这一条不依赖配置。
    """
    llm = ChatDeepSeek(
        model="test-model", api_key="test-key", timeout=12.5, max_retries=7
    )

    assert llm.root_async_client.timeout == 12.5
    assert llm.root_async_client.max_retries == 7


def test_configured_llm_carries_the_budget() -> None:
    from app.agent.llm import llm
    from app.conf.app_config import app_config

    assert llm.request_timeout == app_config.llm.timeout_s
    assert llm.max_retries == app_config.llm.max_retries
