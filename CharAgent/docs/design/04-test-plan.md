# 04 测试计划

> 原则：测试测「代码对不对」（确定性，可 mock）；评估测「输出好不好」（非确定性，LLM-as-judge，P2）。分层 + 默认 mock + 集成测试开关。

## 1. 分层策略

| 层 | 工具 | 内容 | 默认执行 |
|----|------|------|---------|
| 单元 | pytest + pytest-asyncio | 单模块逻辑：model 解析、tool schema 生成、loop 分支、序列化协议、限流算法、锁 | ✅ 全 mock |
| 集成 | pytest + respx | 模块交互：httpx 裸调 vs openai SDK 行为一致（同组测试约束，ADR-0003）；checkpoint 双实现语义对比；SSE 事件流组装 | ✅ 全 mock |
| 真实集成 | pytest（`RUN_INTEGRATION=1`） | 打真实 DeepSeek：httpx 裸调协议字段正确性（tool_calls 结构 / usage / finish_reason / reasoning_content）、流式 delta 累积、reasoner 的 reasoning_content 分离 | 🔶 开关控制 |
| E2E | pytest + httpx | 完整链路：REST → TaskQueue → loop → 工具 → SSE 事件序列断言 | ✅ mock LLM + 打桩外部服务 |
| 前端 | Vitest（可选） | EventSource 事件渲染 | P1 后期 |

## 2. Mock 库设计（#61）

`tests/mock_llm.py` 实现三种模式，实现 `ChatModel` 协议（被测代码零改动）：

| 模式 | 行为 | 用途 |
|------|------|------|
| 固定返回 | 恒返回预设 ModelResponse | 单测分支 |
| 脚本化序列 | 按调用次数依次返回脚本列表（第一次调 A 第二次调 B） | 多轮 loop 测试、轨迹断言 |
| 录制回放 | 真实 API 录一次响应存 JSON 样本，回放时原样返回 | 协议级测试（样本来自真实 DeepSeek） |

确定性：`temperature=0` + `seed` 固定 + 注入随机源（#61），保证同输入同输出。

## 3. 测试矩阵（核心场景 → 断言）

| 场景 | 断言（不止最终答案，还断言轨迹 #62） |
|------|------|
| 单工具调用 | 工具名 / 参数 / tool_result 回填格式 |
| 并行工具调用（#1） | 同一 assistant 消息的多个 tool_call **并发**执行（记录时间戳/顺序，验证非串行）；部分失败时成功结果与失败原因一起回填 |
| 错误自纠错（#2） | 工具报错 → 错误回填 → 模型二次调用成功 |
| 无限循环防护（#3） | max_turns / token 预算 / wall-clock 三种触发点，kill switch 即时打断 |
| 断点续跑（#5） | 快照后中断 → 从 checkpoint 恢复 → 不重复执行已完成动作；time-travel 恢复历史分支 |
| reasoning（#11） | reasoning 增量分离推送；不回填消息历史 |
| 流式事件（#4） | 事件序列快照断言（thinking→tool_call→tool_result→final） |
| HITL（#25） | 挂起 → 快照 → 批准恢复（不重跑）/ 拒绝回填 / 超时降级 |
| 幂等（#13/#17） | 同 request_id 重复提交 → 返回已有结果，副作用不重复 |
| 取消（#18） | cancel 后协程释放、run 状态 cancelled |
| 降级（#19） | LLM 失败 → 模板回复；RAG 失败 → FAQ 匹配 |
| 限流（#22） | 固定/滑动窗口/令牌桶/漏桶边界 |
| checkpoint 双实现（ADR-0002） | 同一事件序列喂 Redis / Postgres saver，恢复结果一致；Postgres 历史可回溯 |

## 4. 快照测试（#63）

| 对象 | 方式 |
|------|------|
| checkpoint 序列化 | JSON 快照对比（schema_version 升级时快照更新 + 旧版本读回测试） |
| 事件流 | SSE 事件序列快照对比（防回归） |
| 契约测试 | 双适配器（httpx / openai SDK）对同一输入产生等价 ModelResponse |

## 5. 验收标准映射

| 阶段 | 验收项（对应 README「分阶段计划」） |
|------|------|
| P0 | 带工具的 agent loop 跑通；checkpoint 可断点续跑；mock LLM 单测通过；轨迹断言 + 快照测试就位 |
| P1 | 客服 demo 端到端跑通（提问→检索→回复→转人工）；SSE 流式输出；高危操作需审批（HITL 全流程：挂起→审批→恢复）；降级路径验证 |
| P2 | 多租户隔离（检索强制过滤测试）；成本追踪；指标告警；多 agent 协作；评估回归；无状态水平扩展 |

## 6. 测试运行

```bash
pytest tests/                          # 单元 + 集成 + E2E（全 mock，CI 默认）
RUN_INTEGRATION=1 pytest tests/integration/   # 真实 API（本地，需 .env 密钥）
pytest tests/test_checkpoint_postgres.py --pg  # Postgres 实现（需本机 PG 运行）
```
