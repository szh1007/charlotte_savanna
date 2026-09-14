"""MockLLM: 三模式 fake ChatModel (Seam 1, #61) + 真实样本录制回放 (issue 09).

**被测代码零改动**: MockLLM 实现 `CharAgent.model.protocol.ChatModel` 协议
(薄协议, 不要求继承), AgentLoop / RetryingChatModel 拿到的仍是同一个
`ChatModel` 类型 —— 测试里换掉的是「大脑」, 换法与真模型完全一样.

三种模式 (api / 行为 / 用途, 对应 design/04-test-plan.md §2):

| 模式 | 构造 | 行为 | 用途 |
|------|------|------|------|
| 固定返回 | `MockLLM.fixed(resp)` | 恒返回同一条 (调多少次都是它) | 单测分支 |
| 脚本化序列 | `MockLLM.scripted([...])` | 按调用次数依次弹, 弹空即报错 | 多轮 loop |
| 录制回放 | `MockLLM.replay("name")` | 回放真实 API 录下的响应原文 | 协议级测试 |

`ScriptedModel` 是 `MockLLM` 的别名 (issue 04 先行版的类名, 保留兼容: 那批
测试文件零改动); `MockLLM(script)` 与 `MockLLM.scripted(script)` 等价.

**录制与回放**(与 issue 01 的约定一致: `ModelResponse.raw` 保留响应原文):
`RecordingChatModel(inner)` 包住真实适配器, 原样记下每次 generate 的请求与
响应原文, `dump()` 落成 JSON 样本 (见 `tests/record_llm_samples.py`); 回放时
用 model/parse.py 的 `parse_chat_completion` 把原文**重新解析**成
`ModelResponse` —— 于是「解析层面对真实响应读得对不对」也被一起回归, 而不是
回放一份别人算好的结论 (样本里若含畸形字段, 回放会在构造期就报错).

生成器另有: 脚本元素可以是 `ModelResponse`, 也可以是
`async (messages) -> ModelResponse` 的可调用 (用于注入轮间逻辑, 比如按当轮
历史决定响应); 脚本耗尽后仍被调用会显式抛错, 提示脚本长度与模型实际调用
次数不匹配, 而非静默返回错误结果.

大白话版: 这是「假大脑」—— 提前写好它每轮该回什么 (或直接用录下来的真话),
测试就不用真调 API, 跑得快、结果稳定. 两个响应工厂 (text_response /
tool_call_response) 都可带可选的 reasoning 与 usage, 好造出真实形态 (比如
「边交代边调工具 + 带思维链」的工具轮), issue 05 的事件测试就靠它造各种剧本.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from CharAgent.model.parse import parse_chat_completion
from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelToolCall,
    Usage,
)

# 脚本元素: 预置响应 或 按当轮 messages 动态产出的异步工厂
ScriptStep = ModelResponse | Callable[[list[ModelMessage]], Awaitable[ModelResponse]]

# 样本目录 (随仓库提交) 与样本格式版本 —— 格式变了要在读入时认出来, 而不是
# 把老样本读出一半字段然后给出一个错的响应
SAMPLE_DIR = Path(__file__).parent / "fixtures" / "llm"
SAMPLE_VERSION = 1


def text_response(
    content: str | None,
    *,
    finish_reason: FinishReason = FinishReason.STOP,
    usage: Usage | None = None,
    reasoning: str | None = None,
) -> ModelResponse:
    """纯文本响应工厂 (默认正常终止); content=None 表示无正文 (length 截断等)."""
    return ModelResponse(
        content=content,
        finish_reason=finish_reason,
        reasoning=reasoning,
        usage=usage,
        model="deepseek-flash",
    )


def tool_call_response(
    *calls: ModelToolCall,
    content: str | None = None,
    finish_reason: FinishReason = FinishReason.TOOL_CALLS,
    usage: Usage | None = None,
    reasoning: str | None = None,
) -> ModelResponse:
    """带 tool_calls 的响应工厂 (模型要调工具, 默认 finish=tool_calls).

    content 可同时给出 (非 None) —— 真实端点上模型常「边叙述边调工具」
    (官方思考模式样例的输出即 content="Let me check..." + tool_calls 并存),
    该形态下叙述进 wire 历史但不进最终答案; reasoning 同为可选 (思考模式下
    工具轮同样带思维链, #11).
    """
    return ModelResponse(
        content=content,
        tool_calls=list(calls),
        finish_reason=finish_reason,
        reasoning=reasoning,
        usage=usage,
        model="deepseek-flash",
    )


def make_tool_call(
    name: str,
    arguments: str = "{}",
    *,
    call_id: str | None = None,
) -> ModelToolCall:
    """ModelToolCall 工厂 (arguments 为原始 JSON 字符串, 与协议保真约定一致)."""
    return ModelToolCall(id=call_id or f"call_{name}", name=name, arguments=arguments)


class MockMode(StrEnum):
    """三种模式 (表见模块 docstring); 用例可断言当前是哪种, 便于排查."""

    FIXED = "fixed"
    SCRIPTED = "scripted"
    REPLAY = "replay"


# ---------------------------------------------------------------------------
# 请求记录与样本 (录制 / 回放)
# ---------------------------------------------------------------------------


def request_record(
    messages: list[ModelMessage],
    tools: list[dict[str, Any]] | None,
    *,
    temperature: float | None,
    top_p: float | None,
    seed: int | None,
    max_tokens: int | None,
    thinking: bool | None,
    reasoning_effort: str | None,
    stream: bool,
) -> dict[str, Any]:
    """一次 generate 的请求记录 (MockLLM 的轨迹数据源 + 样本里的 request 字段).

    messages 走**浅拷贝**: 记录「模型这次看到什么」必须冻结调用时刻的历史,
    否则 AgentLoop 后续 append 会污染先前轮次的轨迹 (列表是同一引用).
    """
    return {
        "messages": list(messages),
        "tools": tools,
        "temperature": temperature,
        "top_p": top_p,
        "seed": seed,
        "max_tokens": max_tokens,
        "thinking": thinking,
        "reasoning_effort": reasoning_effort,
        "stream": stream,
    }


@dataclass(slots=True)
class LLMExchange:
    """样本里的一轮问答: 请求记录 + 上游响应原文 (wire JSON)."""

    request: dict[str, Any]
    response: dict[str, Any]

    def parsed_response(self) -> ModelResponse:
        """响应原文 → ModelResponse (走生产解析函数, 回放即回归解析层)."""
        return parse_chat_completion(self.response)


@dataclass(slots=True)
class LLMSample:
    """一份录制样本: 来源元信息 + 若干轮问答 (issue 09, 回放模式的数据源).

    attributes:
        name: 样本名 (文件名去后缀).
        path: 样本文件路径.
        model: 录制时上游回显的模型名.
        recorded_at: 录制时刻 (ISO 文本; 样本随仓库提交, 便于追溯来源).
        sampling: 录制时的采样参数 (temperature / seed 等, 确定性的证据).
        note: 录制场景说明 (录的是什么路径).
        exchanges: 逐轮的请求与响应原文.
    """

    name: str
    path: Path
    exchanges: list[LLMExchange]
    model: str | None = None
    recorded_at: str | None = None
    sampling: dict[str, Any] = field(default_factory=dict)
    note: str | None = None

    @property
    def requests(self) -> list[dict[str, Any]]:
        """逐轮请求记录 (轨迹断言的素材: 真实样本里模型每轮看到了什么)."""
        return [exchange.request for exchange in self.exchanges]

    @property
    def responses(self) -> list[ModelResponse]:
        """逐轮响应 (已解析); 样本里有畸形字段时在这里就报错."""
        return [exchange.parsed_response() for exchange in self.exchanges]

    def to_payload(self) -> dict[str, Any]:
        """样本 → 可写盘的结构 (写入格式的唯一出处)."""
        return {
            "sample_version": SAMPLE_VERSION,
            "name": self.name,
            "model": self.model,
            "recorded_at": self.recorded_at,
            "note": self.note,
            "sampling": self.sampling,
            "exchanges": [
                {"request": exchange.request, "response": exchange.response}
                for exchange in self.exchanges
            ],
        }


def sample_path(name: str | Path) -> Path:
    """样本名 → 文件路径: 只给名字 (`tool_path` / `tool_path.json`) 时进样本目录.

    带目录的部分 (绝对路径或 `fixtures/llm/x.json` 这种相对路径) 原样使用 ——
    调用方给了路径就是给了路径. **只给名字**时才拼 `SAMPLE_DIR` (绝对路径:
    本模块会被 pytest 从任意 cwd 导入, 相对路径会随 cwd 变化).
    """
    path = Path(name)
    if path.is_absolute() or path.parent != Path("."):
        return path
    stem = path.stem if path.suffix == ".json" else path.name
    return SAMPLE_DIR / f"{stem}.json"


def load_sample(name: str | Path) -> LLMSample:
    """读一份样本 JSON (回放模式与样本断言共用).

    Raises:
        AssertionError: 文件不存在 / 版本不认识 / 结构不成形 —— 都是「样本本身
            有问题」, 用可读信息说清是哪一种, 别让它伪装成被测代码的失败.
    """
    path = sample_path(name)
    if not path.exists():
        raise AssertionError(
            f"样本文件不存在: {path} (先跑 python tests/record_llm_samples.py 录制)"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("sample_version")
    if version != SAMPLE_VERSION:
        raise AssertionError(
            f"样本 {path.name} 的格式版本是 {version!r}, 本代码只认 "
            f"{SAMPLE_VERSION} (样本要随格式一起更新, 别硬读)"
        )
    raw_exchanges = payload.get("exchanges")
    if not isinstance(raw_exchanges, list) or not raw_exchanges:
        raise AssertionError(f"样本 {path.name} 里没有 exchanges (录到空样本?)")
    exchanges: list[LLMExchange] = []
    for index, item in enumerate(raw_exchanges):
        if not isinstance(item, dict) or "response" not in item:
            raise AssertionError(
                f"样本 {path.name} 第 {index + 1} 轮的格式不对: {item!r}"
            )
        exchanges.append(
            LLMExchange(
                request=item.get("request") or {},
                response=item["response"],
            )
        )
    return LLMSample(
        name=payload.get("name") or path.stem,
        path=path,
        model=payload.get("model"),
        recorded_at=payload.get("recorded_at"),
        sampling=payload.get("sampling") or {},
        note=payload.get("note"),
        exchanges=exchanges,
    )


def dump_sample(sample: LLMSample) -> Path:
    """样本写盘 (缩进 + 非 ASCII 原样: 样本要进 code review, 给人读)."""
    sample.path.parent.mkdir(parents=True, exist_ok=True)
    sample.path.write_text(
        json.dumps(sample.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return sample.path


class RecordingChatModel:
    """录制包装: 包住真实 ChatModel, 每次 generate 记下 (请求, 响应原文).

    用途只有录样本 (issue 09): 跑一次真实链路, 把上游响应原文存下来, 之后
    测试用 `MockLLM.replay()` 回放 —— 回放用例零网络零额度, 但走的是真样本.

    要求被包的适配器保留响应原文 (`ModelResponse.raw`; httpx 裸调与 openai
    SDK 两个适配器都保留): 没原文就没得录, 此时显式报错并说清原因.

    attributes:
        exchanges: 逐轮 (请求记录, 响应原文); 与 MockLLM.calls 同构, 便于
            录制完直接当轨迹断言的数据源看.
        responses: 逐轮响应 (已解析) —— 录制脚本靠它检查「这次真录到了想录的
            路径」 (比如模型确实调了那个工具), 免得录完才发现录的是别的东西.
    """

    def __init__(self, inner: Any, *, name: str = "sample") -> None:
        self._inner = inner
        self.name = name
        self.exchanges: list[LLMExchange] = []

    @property
    def calls(self) -> list[dict[str, Any]]:
        """逐轮请求记录 (与 MockLLM.calls 同构: `trace_of` 直接就能用)."""
        return [exchange.request for exchange in self.exchanges]

    @property
    def responses(self) -> list[ModelResponse]:
        """逐轮响应 (已解析); 解析失败会在录制收尾时就炸, 不会拖到用例里."""
        return [exchange.parsed_response() for exchange in self.exchanges]

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """转发给真模型, 记下请求与响应原文 (返回值原样透出)."""
        request = request_record(
            messages,
            tools,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            stream=stream,
        )
        response = await self._inner.generate(
            messages,
            tools,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            stream=stream,
        )
        if response.raw is None:
            raise AssertionError(
                f"第 {len(self.exchanges) + 1} 轮响应没有 raw 原文, 无法录制: "
                f"被包的适配器要保留 ModelResponse.raw (双适配器都保留, 见 "
                f"model/client_httpx.py 与 client_sdk.py)"
            )
        self.exchanges.append(LLMExchange(request=request, response=response.raw))
        return response

    async def aclose(self) -> None:
        """转发关闭 (谁建的谁关仍由调用方决定)."""
        await self._inner.aclose()

    def to_sample(
        self,
        *,
        sampling: Mapping[str, Any] | None = None,
        note: str | None = None,
        recorded_at: str | None = None,
        path: Path | None = None,
    ) -> LLMSample:
        """把已录到的轮次组装成样本对象 (时间由调用方给, 便于复现).

        Args:
            sampling / note / recorded_at: 样本元信息 (录制场景自述).
            path: 落盘位置; None 表示按样本名进 `fixtures/llm/` (测试写临时
                目录时显式给).
        """
        model = None
        for exchange in self.exchanges:
            model = exchange.response.get("model") or model
        return LLMSample(
            name=self.name,
            path=path or sample_path(self.name),
            exchanges=list(self.exchanges),
            model=model,
            recorded_at=recorded_at,
            sampling=dict(sampling or {}),
            note=note,
        )

    def dump(
        self,
        *,
        sampling: Mapping[str, Any] | None = None,
        note: str | None = None,
        recorded_at: str | None = None,
        path: Path | None = None,
    ) -> Path:
        """把录到的轮次写进样本文件 (默认 `fixtures/llm/{name}.json`)."""
        return dump_sample(
            self.to_sample(
                sampling=sampling, note=note, recorded_at=recorded_at, path=path
            )
        )


# ---------------------------------------------------------------------------
# MockLLM 本体
# ---------------------------------------------------------------------------


def _wire_snapshot(value: Any) -> Any:
    """结构归一 (JSON 往返一遍): 比对请求时忽略 dict 顺序等无关差异."""
    return json.loads(json.dumps(value, ensure_ascii=False))


class MockLLM:
    """三模式 fake ChatModel (Seam 1, issue 09 / #61).

    attributes:
        mode: 当前模式 (MockMode).
        calls: 每次 generate 的请求记录 (轨迹断言 #62 的数据源), 每项含
            messages(浅拷贝) / tools / temperature / top_p / seed /
            max_tokens / thinking / reasoning_effort / stream.
        responses: 每次 generate 给出的响应, 与 calls 一一对应 —— 轨迹断言的
            另一半: 模型每轮**决定了什么**. 两半都记的原因: 工具调用只在响应
            里 (最后一轮被刹车拦下的调用不会出现在任何一次请求的 messages
            里), 而「模型看到了什么」只在请求里, 缺一半就断言不全.
        no_tools_calls: 记录 tools=None (未开放工具) 时的调用次数.
        sample: 回放模式下的样本对象 (LLMSample); 其余模式为 None.
    """

    def __init__(
        self,
        script: Sequence[ScriptStep],
        *,
        mode: MockMode = MockMode.SCRIPTED,
        sample: LLMSample | None = None,
        verify_requests: bool = False,
    ) -> None:
        """
        Args:
            script: 逐次调用的响应序列 (固定返回模式只用其中一条).
            mode: 模式标签 (由三个 classmethod 设置).
            sample: 回放模式的样本 (轨迹与失败提示都从它取信息).
            verify_requests: 回放时校验「本次请求」与「录制时的请求」一致
                (messages + tools), 不一致即报错 —— 契约回归的开关 (默认关:
                换过 prompt / 工具描述的回放是有意为之, 不该一律判错).
        """
        self._script: list[ScriptStep] = list(script)
        self.mode = mode
        self.sample = sample
        self.verify_requests = verify_requests
        self.calls: list[dict[str, Any]] = []
        self.responses: list[ModelResponse] = []
        self.no_tools_calls = 0
        # 空脚本不在这里报错: 有一批构造校验用例只需要「一个模型实例」把
        # AgentLoop 建起来 (从不 generate), 空脚本对它们是合法的. 真的被调用
        # 而弹不出响应时, _exhausted_message 会给出可读报错.

    # ------------------------------------------------------------------
    # 三个模式入口
    # ------------------------------------------------------------------

    @classmethod
    def fixed(cls, response: ModelResponse) -> MockLLM:
        """模式一: 恒返回同一条响应 (调用次数不限, 不判耗尽)."""
        return cls([response], mode=MockMode.FIXED)

    @classmethod
    def scripted(cls, script: Sequence[ScriptStep]) -> MockLLM:
        """模式二: 按调用次数依次弹脚本 (弹空即报错, 提示脚本与轮数不匹配)."""
        return cls(script, mode=MockMode.SCRIPTED)

    @classmethod
    def replay(cls, sample: str | Path, *, verify_requests: bool = False) -> MockLLM:
        """模式三: 回放一份录制样本 (响应原文经生产解析函数重建).

        Args:
            sample: 样本名 (如 "tool_path") 或样本文件路径.
            verify_requests: 是否校验本次请求与录制时一致 (见 __init__).

        Returns:
            MockLLM: 逐轮回放样本响应的实例.

        Raises:
            AssertionError: 样本缺失 / 版本不符 / 结构不成形 (见 load_sample).
        """
        loaded = load_sample(sample)
        responses = loaded.responses  # 构造期就解析: 样本坏了当场报错
        return cls(
            list(responses),
            mode=MockMode.REPLAY,
            sample=loaded,
            verify_requests=verify_requests,
        )

    # ------------------------------------------------------------------
    # ChatModel 协议
    # ------------------------------------------------------------------

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """ChatModel 协议实现: 记录请求 → 按模式给出响应.

        三种模式的分支: 固定返回恒回同一条; 回放先校验请求(可选)再弹响应;
        脚本化直接弹脚本 —— 弹空时抛错, 说清是脚本耗尽还是样本轮次用尽.
        """
        if tools is None:
            self.no_tools_calls += 1
        self.calls.append(
            request_record(
                messages,
                tools,
                temperature=temperature,
                top_p=top_p,
                seed=seed,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                stream=stream,
            )
        )
        turn = len(self.calls)
        if self.mode is MockMode.REPLAY and self.verify_requests:
            self._verify_request(turn, messages, tools)
        if self.mode is MockMode.FIXED:
            if not self._script:
                # fixed() 一定给了一条; 走到这里说明是手工构造出来的空固定脚本
                raise AssertionError(self._exhausted_message(turn, messages))
            response = await self._response_of(self._script[0], messages)
        elif self._script:
            response = await self._response_of(self._script.pop(0), messages)
        else:
            raise AssertionError(self._exhausted_message(turn, messages))
        self.responses.append(response)
        return response

    async def aclose(self) -> None:
        """释放资源 (协议要求; fake 无资源可释放)."""

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _response_of(
        self,
        step: ScriptStep,
        messages: list[ModelMessage],
    ) -> ModelResponse:
        """弹出一条脚本元素 (可调用元素按当轮 messages 现算)."""
        if callable(step):
            return await step(messages)
        return step

    def _exhausted_message(self, turn: int, messages: list[ModelMessage]) -> str:
        """脚本 / 样本耗尽的可读报错 (说清是哪种模式、还差多少)."""
        if self.mode is MockMode.REPLAY and self.sample is not None:
            return (
                f"样本 {self.sample.path.name} 只有 {len(self.sample.exchanges)} 轮, "
                f"但模型第 {turn} 次被调用: 测试里的脚本轮数比录制时多 "
                f"(改测试或重录样本), trace 请求消息: "
                f"{messages[-1] if messages else '(空)'}"
            )
        hint = (
            "固定返回模式要求一条响应 (MockLLM.fixed(...))"
            if self.mode is MockMode.FIXED
            else "请检查测试脚本条数是否与预期轮数一致"
        )
        return (
            f"MockLLM 脚本已耗尽, 但模型第 {turn} 次被调用: {hint} "
            f"(trace 请求消息: {messages[-1] if messages else '(空)'})"
        )

    def _verify_request(
        self,
        turn: int,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None,
    ) -> None:
        """回放校验: 本次请求与录制时的请求一致吗 (契约回归).

        只比 messages 与 tools —— 它们是 wire 契约本身; 采样参数属测试自己
        的配置 (回放用例本来就该自己钉死 temperature / seed), 不参与比对.
        """
        assert self.sample is not None  # 回放模式必有样本 (构造期保证)
        index = turn - 1
        if index >= len(self.sample.exchanges):
            return  # 轮次越界由 _exhausted_message 报, 这里只管比对得上的那几轮
        recorded = self.sample.exchanges[index].request
        for field_name, actual in (("messages", messages), ("tools", tools)):
            expected = recorded.get(field_name)
            if _wire_snapshot(actual) != _wire_snapshot(expected):
                raise AssertionError(
                    f"回放第 {turn} 轮的请求与录制时不一致 ({field_name}):\n"
                    f"  录制: {_brief(expected)}\n"
                    f"  本次: {_brief(actual)}\n"
                    f"  这是 wire 契约回归 (#63): 若改动是有意的, 请重录样本 "
                    f"({self.sample.path.name})"
                )


def _brief(value: Any, *, limit: int = 400) -> str:
    """比对失败时的短摘要 (整份 messages 太长, 打出来没人看)."""
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


# 兼容别名: issue 04 起的类名 (那批测试文件零改动, 见模块 docstring)
ScriptedModel = MockLLM
