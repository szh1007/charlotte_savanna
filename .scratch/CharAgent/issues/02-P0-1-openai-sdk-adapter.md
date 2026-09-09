# 02-P0-1 — openai SDK 适配器 + 双适配器一致性契约测试

**What to build:** 用 openai SDK 实现同一 ChatModel 协议（同 DeepSeek base_url），与 httpx 裸调实现并列。同一组契约测试约束两个实现：对同一输入产生等价的 ModelResponse（tool_calls、reasoning、finish_reason、usage 全字段等价），演示「SDK 帮你藏了什么」（ADR-0003）。

**Blocked by:** 01

**Status:** done

- [x] openai SDK 适配器实现 ChatModel 协议（非流式 + 流式）
- [x] 契约测试：httpx 裸调 vs openai SDK 对同一 mock 输入产生等价 ModelResponse（#63 契约测试）
- [x] reasoning_content 两实现均正确分离（有/无推理字段两分支）
- [x] 流式 delta 累积结果与非流式一致

## Comments

**2026-09-09 完成**：实现位于 `CharAgent/model/sdk.py`（OpenAIChatModel + openai_chat_model_from_env 工厂）。

- 架构：SDK 响应 / chunk 对象 `model_dump()` 还原为 wire 结构后，喂给与 httpx 裸调**同一组解析纯函数**（parsing / sse），双适配器一致性由「同一解析层」兜底，SDK 只藏传输 / SSE 行解析 / JSON 反序列化（教学对比 ADR-0003）。
- 关键探针发现（openai SDK 2.48.0）：显式 `None` 参数不剔除（会发 `"tools": null`），故请求组装与 `http._resolve_payload` 保持「None 不携带」语义；SDK `max_retries=0` 关闭内建重试（重试统一归 P0-5 retry 层）；`APIResponseValidationError`（响应畸形）映射 `ModelProtocolError`。
- 配套改动：错误体提取 `extract_error_message` 自 http.py 提升至 parsing.py 双端共享；`_strip_provider_prefix` 公开化 `strip_provider_prefix`。
- 验收证据：新增 14 契约用例（请求体 / 非流式 / 流式 / 畸形语义等价）+ 10 SDK 单测 + 1 双工厂同配置用例，全量 88 mock 用例通过；RUN_INTEGRATION=1 真实 API 6 用例通过（含 SDK reasoning 分离与流式累积，SDK extra 字段在真实端点保留）。
- **code-review 修复**（双轴审查后）：
  - 畸形 JSON 泄漏修复：SDK 对 2xx 坏 JSON body 抛裸 `json.JSONDecodeError`、对非 JSON content-type 宽松返回 str（均不经 APIError 层级，原 `APIResponseValidationError` 分支几乎不可达）——generate / `_accumulate_stream` 补 catch 统一映射 `ModelProtocolError`，契约新增「非流式坏 body（json/html 两变体）+ 流式坏行」3 用例让双端畸形容错等价显形。
  - 共享配置收编 `CharAgent/model/config.py`：`DEFAULT_BASE_URL` / `DEFAULT_MODEL` / `strip_provider_prefix` 自 http.py 移入，http/sdk 两适配器并列 import（消除兄弟模块反向依赖）。
  - 测试样本 `text_completion_json` / `sse_chunk` 提升 `tests/helpers.py`（3 文件共用，越过 DRY 阈值）；fixture `model_pair` 返回注解 `tuple[HttpXChatModel, OpenAIChatModel]`；`max_retries=0` 原因与 SDK 畸形行为注释修正。
