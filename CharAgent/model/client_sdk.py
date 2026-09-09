"""openai SDK 适配器: SDK 封装传输 / SSE 行解析 / JSON 反序列化 (ADR-0003, issue 02).

教学对比 (与 client_httpx.py 并列, 同一 ChatModel 协议):
- client_httpx.py: 手拼 /chat/completions 请求体, 手解 SSE 文本行,
  手调 json.loads, 自己把 wire JSON 解析为 ModelResponse
- client_sdk.py:   参数传给 client.chat.completions.create, 传输 / SSE 解析 /
  反序列化全由 SDK 完成 (内建重试已关, max_retries=0, 统一归 P0-5 retry 层);
  拿到响应 / chunk 对象后 model_dump() 还原为 wire 结构, 喂给与 client_httpx.py
  同一组解析纯函数 (parse / stream) —— 「SDK 帮你藏了什么」: 藏的是传输与
  序列化细节, 协议字段语义 (tool_calls / reasoning / usage) 不变.

两适配器行为一致性 (对同一输入产出等价 ModelResponse) 由契约测试
tests/test_model_contract.py 约束 (#63); 模型名 / base_url / 采样参数语义
与 client_httpx.py 对齐 (DeepSeek OpenAI 兼容端点), 默认配置见 utils/config.py.

注意: openai SDK 对显式传入的 None 参数不会剔除 (会发 "tools": null 等),
因此请求参数组装与 client_httpx._resolve_payload 相同的「None 不携带」语义,
否则与服务端默认行为产生差异.

配置入口: openai_chat_model_from_env 与 client_httpx.chat_model_from_env 同语义,
同一个根 .env 的 DEEPSEEK_* 变量按需构建任一适配器.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import openai

from CharAgent.model.parse import extract_error_message, parse_chat_completion
from CharAgent.model.stream import (
    _StreamAccumulator,
    apply_sse_chunk,
    build_stream_response,
)
from CharAgent.model.utils.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    strip_provider_prefix,
)
from CharAgent.model.utils.errors import (
    ModelConfigError,
    ModelConnectionError,
    ModelError,
    ModelProtocolError,
    ModelStatusError,
    ModelTimeoutError,
)
from CharAgent.model.utils.types import ModelMessage, ModelResponse, ToolSpec


def _map_api_error(exc: openai.APIError) -> ModelError:
    """openai SDK 异常层级 -> 本项目错误语义 (与 client_httpx.py 一致, 供 retry 判断).

    覆盖: 超时 / 连接失败 -> 瞬态, 非 2xx -> ModelStatusError (429 / 5xx 瞬态,
    其余 4xx 永久).

    注: APIResponseValidationError 分支 (SDK schema 校验失败) 在 2.48 中几乎
    不可达 —— SDK 对 2xx 畸形 JSON 抛裸 json.JSONDecodeError, 由 generate /
    _accumulate_stream 层兜底映射; 本分支保留作为 SDK 版本行为差异时的兜底.
    """
    if isinstance(exc, openai.APITimeoutError):
        return ModelTimeoutError(f"模型 API 请求超时: {exc}")
    if isinstance(exc, openai.APIConnectionError):
        return ModelConnectionError(f"模型 API 连接失败: {exc}")
    if isinstance(exc, openai.APIStatusError):
        return ModelStatusError(
            exc.status_code, extract_error_message(exc.response.text)
        )
    if isinstance(exc, openai.APIResponseValidationError):
        return ModelProtocolError(f"模型响应结构不符合 SDK schema: {exc}")
    return ModelConnectionError(f"openai SDK 调用失败: {exc}")


class OpenAIChatModel:
    """openai SDK 适配器 (ADR-0003): 与 HttpXChatModel 同一协议, 同一行为契约.

    教学定位: 展示 SDK 如何隐藏传输 / SSE 解析细节 —— 本类只负责传参与
    把 SDK 返回对象 model_dump() 回 wire 结构, 字段语义解析仍走 model 包
    公共纯函数 (与 httpx 裸调共享), 这是双适配器行为一致的根基.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
    ) -> None:
        """构造 openai SDK 适配器 (底层 client 惰性发请求, 复用连接池).

        Args:
            api_key: DeepSeek API Key (必填, 参考根 .env.example).
            base_url: API 根地址, 可含 /v1 路径, 与 client_httpx.py 同语义.
            model: OpenAI 兼容模型名 (裸名, 无 provider 前缀).
            timeout: 单次请求超时秒数, 默认 60, 语义同 HttpXChatModel.timeout.
            temperature: 默认采样温度, 调用级可覆盖 (#68).
            top_p: 默认核采样参数, 调用级可覆盖 (#68).
            seed: 默认随机种子, 固定后同输入同输出 (#61 / #68).

        Raises:
            ModelConfigError: api_key 为空时.
        """
        if not api_key:
            raise ModelConfigError(
                "api_key 不能为空 (配置参考根 .env.example 的 DEEPSEEK_API_KEY)"
            )
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self._temperature = temperature
        self._top_p = top_p
        self._seed = seed
        # max_retries=0: 重试策略统一归 P0-5 retry 层 (与 client_httpx.py 一致),
        # SDK 内建重试会绕过业务重试的退避 / 熔断 / 幂等设计
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
        )

    def _resolve_kwargs(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None,
        *,
        stream: bool,
        temperature: float | None,
        top_p: float | None,
        seed: int | None,
    ) -> dict[str, Any]:
        """组装 create() 调用参数: 与 client_httpx._resolve_payload 同语义.

        None 不携带 (SDK 对显式 None 不剔除, 会发 "tools": null 等服务端
        可能不接受的 null 值, 因此不能直接透传).
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = tools
        for key, call_value, instance_default in (
            ("temperature", temperature, self._temperature),
            ("top_p", top_p, self._top_p),
            ("seed", seed, self._seed),
        ):
            resolved = call_value if call_value is not None else instance_default
            if resolved is not None:
                kwargs[key] = resolved
        if stream:
            # 请求尾部追加 usage chunk, 流式累积结果才有 token 计量
            # (#11 reasoning token 计入成本)
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """见 ChatModel.generate. SDK 返回对象 model_dump() 回 wire 结构后,
        走与 client_httpx.py 相同的解析纯函数 (parse_chat_completion / apply_sse_chunk).
        """
        kwargs = self._resolve_kwargs(
            messages,
            tools,
            stream=stream,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
        )
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            raise _map_api_error(exc) from exc
        except json.JSONDecodeError as exc:
            # SDK 对 2xx + content-type json 但 body 非法的响应抛裸 JSONDecodeError
            # (不经 APIError 层级), 与 client_httpx.py 的
            # 「非 JSON -> ModelProtocolError」对齐
            raise ModelProtocolError("非流式响应不是合法 JSON") from exc
        if not stream:
            if not hasattr(completion, "model_dump"):
                # SDK 对 content-type 非 JSON 的 2xx 响应宽松返回原始文本 (str),
                # 同样按协议畸形处理, 不把裸 str 泄漏给调用方
                raise ModelProtocolError("非流式响应不是合法 JSON 对象")
            return parse_chat_completion(completion.model_dump())
        return await self._accumulate_stream(completion)

    async def _accumulate_stream(self, stream: Any) -> ModelResponse:
        """SDK 流式 chunk 逐块累积 (内容 / reasoning / tool_calls 分片).

        chunk.model_dump() 后喂给与 client_httpx._generate_stream 相同的
        apply_sse_chunk / build_stream_response —— 同一累积状态机约束
        双适配器流式结果一致 (issue 02 契约).
        """
        accumulator = _StreamAccumulator()
        try:
            async for chunk in stream:
                apply_sse_chunk(accumulator, chunk.model_dump())
        except openai.APIError as exc:
            raise _map_api_error(exc) from exc
        except json.JSONDecodeError as exc:
            # 流内坏 JSON 行: SDK 迭代解析时抛裸 JSONDecodeError,
            # 文案与 client_httpx.py 的 SSE 畸形报错同语义 (含前 100 字符上下文)
            malformed = getattr(exc, "doc", "") or str(exc)
            raise ModelProtocolError(
                f"SSE chunk 不是合法 JSON: {str(malformed)[:100]!r}"
            ) from exc
        finally:
            await stream.close()
        return build_stream_response(accumulator)

    async def aclose(self) -> None:
        """释放底层连接池 (进程退出 / server 优雅停机时调用)."""
        await self._client.close()

    async def __aenter__(self) -> OpenAIChatModel:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def openai_chat_model_from_env(
    env: Mapping[str, str] | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> OpenAIChatModel:
    """从环境变量构建 SDK 适配器: 与 client_httpx.chat_model_from_env 同语义.

    同一组根 .env 变量 (DEEPSEEK_API_KEY / DEEPSEEK_API_BASE /
    DEEPSEEK_MODEL_NAME), 按需选择 httpx 裸调或 openai SDK 实现;
    显式覆盖参数优先级最高, 空值不静默回退 env.

    Args:
        env: 环境变量映射, 默认 os.environ (便于测试注入).
        api_key: 显式覆盖 DEEPSEEK_API_KEY (优先级最高, 空值不静默回退).
        base_url: 显式覆盖 DEEPSEEK_API_BASE.
        model: 显式覆盖 DEEPSEEK_MODEL_NAME (自动剥离 provider 前缀).

    Raises:
        ModelConfigError: DEEPSEEK_API_KEY 缺失或为空.
    """
    env = os.environ if env is None else env
    key = api_key if api_key is not None else env.get("DEEPSEEK_API_KEY")
    if not key:
        raise ModelConfigError(
            "DEEPSEEK_API_KEY 未配置, 无法创建模型客户端 (参考根 .env.example)"
        )
    resolved_base = (
        base_url
        if base_url is not None
        else env.get("DEEPSEEK_API_BASE") or DEFAULT_BASE_URL
    )
    resolved_model = (
        model if model is not None else env.get("DEEPSEEK_MODEL_NAME") or DEFAULT_MODEL
    )
    return OpenAIChatModel(
        api_key=key,
        base_url=resolved_base,
        model=strip_provider_prefix(resolved_model),
    )
