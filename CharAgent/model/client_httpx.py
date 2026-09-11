"""httpx 裸调适配器: 自己拼 POST /chat/completions, 解析响应与 SSE 流 (ADR-0003).

教学定位: 本类显式展示 SDK 隐藏的协议细节 (请求体 / tool_calls 结构 / delta 累积);
行为与 openai SDK 适配器 (client_sdk.py) 等价, issue 02 契约测试约束.

- _resolve_payload: 组装请求体 (调用级参数优先于实例默认, None 不携带)
- _post: 请求发送 + 错误语义映射 (超时/连接 -> 瞬态, 非 2xx -> ModelStatusError)
- chat_model_from_env: 从根 .env 的 DEEPSEEK_* 变量构建 (LangChain 前缀剥离)
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from CharAgent.model.parse import extract_error_message, parse_chat_completion
from CharAgent.model.stream import (
    StreamAccumulator,
    apply_sse_chunk,
    build_stream_response,
)
from CharAgent.model.utils.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    check_thinking_params,
    strip_provider_prefix,
)
from CharAgent.model.utils.errors import (
    ModelConfigError,
    ModelConnectionError,
    ModelProtocolError,
    ModelStatusError,
    ModelTimeoutError,
)
from CharAgent.model.utils.types import ModelMessage, ModelResponse, ToolSpec


class HttpXChatModel:
    """httpx 裸调实现: 自己拼 POST /chat/completions, 解析响应与 SSE 流 (ADR-0003).

    教学定位: 本类显式展示 SDK 隐藏的协议细节 (请求体 / tool_calls 结构 / delta 累积);
    行为与 openai SDK 适配器等价 (issue 02 契约测试约束).
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
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        """构造 httpx 裸调适配器 (请求实例化后按需发出, 复用底层连接池).

        Args:
            api_key: DeepSeek API Key (必填, 参考根 .env.example).
            base_url: API 根地址, 可含 /v1 路径 (如 https://host/v1),
                统一拼接 /chat/completions.
            model: OpenAI 兼容模型名 (裸名, 无 provider 前缀).
            timeout: 单次请求超时秒数, 默认 60 (P1-3 分层超时的 model 层).
            temperature: 默认采样温度, 调用级可覆盖 (#68);
                **思考模式下不生效** (上游忽略, 不报错).
            top_p: 默认核采样参数, 调用级可覆盖 (#68);
                **思考模式下下限 0.95**, 非思考模式恒为 1.0.
            seed: 默认随机种子 (#61 / #68); 思考模式下仅 content 可复现.
            max_tokens: 默认单次输出上限. 官方取值 1 ~ 384K (393216); 不传时
                非思考默认 8K, 思考默认 64K (effort=max 时 128K). 思维链与
                正文共享该配额 (reasoning_tokens 计入 completion_tokens).
            thinking: 默认思考模式开关 (True/False), None 走上游默认 (开启).
            reasoning_effort: 默认思考强度 (low/high/max; 兼容别名
                minimal/medium/xhigh/ultra 由上游归一), None 走上游默认 (high).
                另接受 "none" —— 与 thinking=False 等效的**第二条关闭路径**,
                两者同时显式传入且方向相反时构造 / 调用期报 ModelConfigError.

        Raises:
            ModelConfigError: api_key 为空时.
        """
        if not api_key:
            raise ModelConfigError(
                "api_key 不能为空 (配置参考根 .env.example 的 DEEPSEEK_API_KEY)"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._temperature = temperature
        self._top_p = top_p
        self._seed = seed
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        self._client = httpx.AsyncClient(timeout=timeout)

    @property
    def _completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _resolve_payload(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None,
        *,
        stream: bool,
        temperature: float | None,
        top_p: float | None,
        seed: int | None,
        max_tokens: int | None,
        thinking: bool | None,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        """组装请求体: 调用级参数优先于实例默认, 均为 None 时不携带 (走服务端默认)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        for key, call_value, instance_default in (
            ("temperature", temperature, self._temperature),
            ("top_p", top_p, self._top_p),
            ("seed", seed, self._seed),
            ("max_tokens", max_tokens, self._max_tokens),
        ):
            resolved = call_value if call_value is not None else instance_default
            if resolved is not None:
                payload[key] = resolved
        resolved_thinking = thinking if thinking is not None else self._thinking
        resolved_effort = (
            reasoning_effort if reasoning_effort is not None else self._reasoning_effort
        )
        # thinking 与 reasoning_effort 是两条独立的思考模式开关路径, 先校验
        # 取值合法且不互相矛盾再发请求 (详见 utils/config.check_thinking_params)
        check_thinking_params(resolved_thinking, resolved_effort)
        if resolved_thinking is not None:
            # 上游在 body 顶层识别 thinking 对象; bool 归一为 enabled/disabled
            payload["thinking"] = {
                "type": "enabled" if resolved_thinking else "disabled"
            }
        if resolved_effort is not None:
            payload["reasoning_effort"] = resolved_effort
        if stream:
            # 请求尾部追加 usage chunk, 流式累积结果才有 token 计量
            # (#11 reasoning token 计入成本)
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _post(self, payload: dict[str, Any], *, stream: bool) -> httpx.Response:
        """
        发送请求并按错误语义映射: 超时/连接错误 -> 瞬态异常,
        非 2xx -> ModelStatusError.
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            if stream:
                request = self._client.build_request(
                    "POST", self._completions_url, headers=headers, json=payload
                )
                response = await self._client.send(request, stream=True)
            else:
                response = await self._client.post(
                    self._completions_url, headers=headers, json=payload
                )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                f"模型 API 请求超时: {self._completions_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelConnectionError(f"模型 API 连接失败: {exc}") from exc
        if response.status_code >= 400:
            await response.aread()  # 读取错误体 (流式响应同样适用)
            raise ModelStatusError(
                response.status_code, extract_error_message(response.text)
            )
        return response

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """
        见 ChatModel.generate. stream=True 时逐行解析 SSE,
        内部累积 delta 后返回完整响应.
        """
        payload = self._resolve_payload(
            messages,
            tools,
            stream=stream,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        if not stream:
            response = await self._post(payload, stream=False)
            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                raise ModelProtocolError("非流式响应不是合法 JSON") from exc
            return parse_chat_completion(data)
        return await self._generate_stream(payload)

    async def _generate_stream(self, payload: dict[str, Any]) -> ModelResponse:
        """
        SSE 流式: 逐行解析 data: 块, delta 累积
        (内容 / reasoning / tool_calls arguments).
        """
        response = await self._post(payload, stream=True)
        accumulator = StreamAccumulator()
        try:
            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped or stripped.startswith(":"):
                    continue  # SSE 注释 / 心跳行
                if not stripped.startswith("data:"):
                    continue  # event: 行 (OpenAI 流式固定事件类型, 无信息量)
                data_text = stripped[len("data:") :].strip()
                if data_text == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_text)
                except json.JSONDecodeError as exc:
                    raise ModelProtocolError(
                        f"SSE chunk 不是合法 JSON: {data_text[:100]!r}"
                    ) from exc
                apply_sse_chunk(accumulator, chunk)
        except ModelProtocolError:
            raise
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError("模型 API 流式响应超时") from exc
        except httpx.HTTPError as exc:
            raise ModelConnectionError(f"SSE 流中途断开: {exc}") from exc
        finally:
            await response.aclose()
        return build_stream_response(accumulator)

    async def aclose(self) -> None:
        """释放底层 httpx 连接池 (进程退出 / server 优雅停机时调用)."""
        await self._client.aclose()

    async def __aenter__(self) -> HttpXChatModel:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def chat_model_from_env(
    env: Mapping[str, str] | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> HttpXChatModel:
    """从环境变量构建 httpx 适配器:
    DEEPSEEK_API_KEY / DEEPSEEK_API_BASE / DEEPSEEK_MODEL_NAME.

    Args:
        env: 环境变量映射, 默认 os.environ (便于测试注入).
        api_key / base_url / model: 显式覆盖环境变量
            (优先级最高, 空值不静默回退 env 而直接报错 / 生效).

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
    return HttpXChatModel(
        api_key=key,
        base_url=resolved_base,
        model=strip_provider_prefix(resolved_model),
    )
