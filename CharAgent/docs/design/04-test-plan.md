# 04 测试计划

> 原则：测试测「代码对不对」（确定性，可 mock）；评估测「输出好不好」（非确定性，LLM-as-judge，P2）。分层 + 默认 mock + 集成测试开关。

## 1. 分层策略

| 层 | 工具 | 内容 | 默认执行 |
|----|------|------|---------|
| 单元 | pytest + pytest-asyncio | 单模块逻辑：model 解析、tool schema 生成、loop 分支、序列化协议、CLI 的命令解析 / 事件渲染 / 参数解析、限流算法、锁（后两项待 P1） | ✅ 全 mock |
| 集成 | pytest + respx | 模块交互：httpx 裸调 vs openai SDK 行为一致（同组测试约束，ADR-0003）；checkpoint 多实现语义对比；SSE 事件流组装 | ✅ 全 mock |
| 真实集成 | pytest（`-m integration`） | 打真实 DeepSeek：httpx 裸调协议字段正确性（tool_calls 结构 / usage / finish_reason / reasoning_content）、流式 delta 累积、思考模式下 reasoning_content 与正文分离；agent loop 多轮工具路径与强制截断 | 🔶 marker 默认排除 |
| E2E（P0 已有） | pytest | **CLI 端到端**：`main()` → 会话 → loop → 工具 → 事件流 → 退出码；含「Ctrl-C 打断 → `/resume` → 已完成的工具不重跑」整条验收路径（`tests/test_client_app.py`） | ✅ mock LLM（真实 API 演示手动跑） |
| E2E（P1 规划） | pytest + httpx | 完整链路：REST → TaskQueue → loop → 工具 → SSE 事件序列断言 | P1-1 落地后接本节基建 |
| 前端 | Vitest（可选） | EventSource 事件渲染 | P1 后期 |

## 2. Mock 库设计（#61）

`tests/mock_llm.py` 实现三种模式，实现 `ChatModel` 协议（被测代码零改动）；
`ScriptedModel` 是 `MockLLM` 的兼容别名（issue 04 那批用例零改动）：

| 模式 | 构造 | 行为 | 用途 |
|------|------|------|------|
| 固定返回 | `MockLLM.fixed(resp)` | 恒返回同一条响应（调多少次都是它） | 单测分支 |
| 脚本化序列 | `MockLLM.scripted([...])` | 按调用次数依次弹脚本（弹空即报错） | 多轮 loop / 轨迹断言 |
| 录制回放 | `MockLLM.replay("tool_path")` | 回放真实 API 录下的响应原文 | 协议级测试 |

- **样本录制**：`python tests/record_llm_samples.py`（打真实端点、消耗额度；产出
  `tests/fixtures/llm/*.json` 随仓库提交）。录制器包住真适配器记下「请求记录 +
  响应原文」，并当场体检样本（工具轮确实调了工具 / 工具结果确实回填 / 思考样本
  至少一轮带思维链），不合格以退出码 1 报出。
- **样本回放**：响应原文经 `parse_chat_completion` **重新解析** —— 于是「解析层
  对真实响应读得对不对」也一起被回归，而不是回放一份别人算好的结论；样本坏了
  在构造期就报错。`verify_requests=True` 时额外比对「本次请求」与「录制时的
  请求」（messages + tools），是 wire 契约的回归开关。
- 三种模式都记录两半数据：`calls`（模型每轮看到什么）与 `responses`（模型每轮
  决定了什么）—— 轨迹断言（#62）的取数口，见下节。

确定性：`temperature=0` + `seed` 固定 + 注入时间 / 随机源（#61），保证同输入
同输出。落到代码是三处注入缝：`trace_assertions.pinned_sampling()` 给采样参数、
`assert_pinned_sampling()` 断每轮都带上；`doubles.FakeClock` / `RecordingSleep`
注入时钟（耗时字段确定）；`doubles.FixedRandom` 注入抖动随机源（重试退避序列
确定，见 `test_retry_policy.py`）。**限定**：思考模式下 `temperature` 不生效、
`top_p` 下限 0.95，`seed` 仅保证 content 可复现（reasoning 不可复现）—— 需要
逐字复现的用例（快照 / 回放）一律走非思考模式。

## 3. 测试矩阵（核心场景 → 断言）

| 场景 | 断言（不止最终答案，还断言轨迹 #62） |
|------|------|
| 单工具调用 | 工具名 / 参数 / tool_result 回填格式 |
| 并行工具调用（#1） | 同一 assistant 消息的多个 tool_call **并发**执行（记录时间戳/顺序，验证非串行）；部分失败时成功结果与失败原因一起回填 |
| 错误自纠错（#2） | 工具报错 → 错误回填 → 模型二次调用成功 |
| 无限循环防护（#3） | max_turns / token 预算 / wall-clock 三种触发点，kill switch 即时打断 |
| 断点续跑（#5） | 快照后中断 → 从 checkpoint 恢复 → 不重复执行已完成动作；time-travel 恢复历史分支 |
| reasoning（#11） | reasoning 增量分离推送（前端折叠展示）；回填 wire 历史（官方文档要求带 `tools` 时回传，称缺失即 400；本机实测 2026-09-11 未强制） |
| 流式事件（#4） | 事件序列快照断言（thinking→tool_call→tool_result→final） |
| HITL（#25） | 挂起 → 快照 → 批准恢复（不重跑）/ 拒绝回填 / 超时降级 |
| 幂等（#13/#17） | 同 request_id 重复提交 → 返回已有结果，副作用不重复 |
| 取消（#18） | cancel 后协程释放、run 状态 cancelled |
| 降级（#19） | LLM 失败 → 模板回复；RAG 失败 → FAQ 匹配 |
| 限流（#22） | 固定/滑动窗口/令牌桶/漏桶边界 |
| checkpoint 双实现（ADR-0002） | 同一事件序列喂 Redis / Postgres saver，恢复结果一致；Postgres 历史可回溯 |

轨迹断言写法（#62）：`trace_of(model).assert_tool_calls([("query_order", {...})])`
—— 期望项给工具名或 `(名字, 参数字典)`（参数按子集匹配）；另有 `assert_turn_count`
/ `assert_tool_names` / `assert_tool_result_backfilled`。工具与用例在
`tests/trace_assertions.py`（自测 `tests/test_trace.py`，含反路）。

**尚未落地**（都属「生产代码还没写」或「P1/P2 阶段才做」，不是漏做）：

| 项 | 归属 | 说明 |
|----|------|------|
| HITL 审批（挂起 → 批准恢复 / 拒绝回填 / 超时降级） | P1-7 | checkpoint 的挂起落盘与补做已在 issue 07 贯通，审批面本身没有生产代码 |
| 降级（模板回复 / FAQ 匹配） | P1-8 | server 层能力 |
| 限流边界（固定 / 滑动窗口 / 令牌桶 / 漏桶） | P1-5 | `ratelimit.py` 尚未创建 |
| 非确定性统计（多次运行看通过率，而非单次通过 #62） | P1 评估 | 默认用例全替身、本身确定，没有可统计的对象；真实端点侧的重复试验归 P2-8 评估体系 |

P1 这三项落地时直接接本节基建（同一 `ChatModel` seam）：脚本化序列造剧本、
轨迹断言断过程、快照锁形状。

## 4. 快照测试（#63）

落盘 / 比对机器在 `tests/snapshots.py`，快照文件在 `tests/fixtures/snapshots/`：

| 对象 | 落点 | 说明 |
|------|------|------|
| checkpoint 序列化 | `fixtures/checkpoint_v*.json`（issue 07）+ `snapshots/checkpoint_frames_tool_path.json` | 前者锁格式（含老版本读回），后者锁 loop 每轮**真正落盘**的那两帧 |
| 事件流 | `snapshots/event_stream_tool_path.json` | 真实样本回放出的事件序列（逐字段载荷；耗时为 `<ms>` 占位） |
| 契约测试 | `tests/test_model_contract.py` + `tests/test_replay_contract.py` | 双适配器对同一 wire 响应产出等价 ModelResponse；后者用**真实样本**，且把回放路径一并纳入三方对比 |

三条使用约定：

- **首次运行**：快照不存在 → 生成文件并让用例**失败**（新快照等于没有防线，得有人看过一眼）
- **有意改形状**：`UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py` 覆盖，改动随 commit 一起 review
- **每次都不一样的东西不许进快照**：时间 / uuid / 耗时先归一（`project_events` /
  `normalize_checkpoint_record` 换成 `<ms>` `<id>` `<ts>`），否则快照用例会变成偶发

## 5. 验收标准映射

| 阶段 | 验收项（对应 DESIGN.md「分阶段计划」） |
|------|------|
| P0 | 带工具的 agent loop 跑通；checkpoint 可断点续跑；mock LLM 单测通过；轨迹断言 + 快照测试就位；CLI 端到端可用（`main()` 的 E2E 层 + 真实端点手动演示，见 §1 / §6） |
| P1 | **框架侧**：SSE 流式输出；状态机/取消（含两种等待态）；熔断超时；幂等/Saga 原语/锁；护栏；**运行时上下文注入与挂起信号通道**（ADR-0010）；HITL 四条路径（恢复 / 拒绝 / 超时 / 用户确认）；审计**工具调用粒度**；降级**分类与映射**。**业务侧**（客服 demo 端到端 / 退款审批 / 转人工接管 / 降级话术 / 数据访问粒度审计）由 `CharService/` 承担 —— 见 `.scratch/CharService/PRD.md` §5 |
| P2 | 多租户隔离（检索强制过滤测试）；成本追踪；指标告警；多 agent 协作；评估回归；无状态水平扩展 |

## 6. 测试运行

```bash
pytest tests/                          # 单元 + E2E（全替身，默认；四个 marker 都被 addopts 排除）
pytest -m integration                  # 真实 API（本地，需 .env 密钥；会消耗额度）
pytest -m pg                           # checkpoint 的 Postgres 实现（需本机 PG 运行）
pytest -m redis                        # checkpoint 的 Redis 实现（需本机 Redis 运行）
pytest -m pg_db                        # 数据层：仓储 + alembic 迁移（需本机 PG 运行）

python tests/record_llm_samples.py     # 重录 LLM 样本（真实端点，消耗额度；产出随仓库提交）
UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py   # 有意改形状后覆盖快照
```

CLI 的演示与冒烟（在**仓库根**跑，会消耗额度）：

```bash
python -m CharAgent.client -q "现在几点?"                    # 带工具问答, 退出码 0/1/130
python -m CharAgent.client --no-thinking --backend postgres --thread-id demo -q "..."
python -m CharAgent.client --backend postgres --thread-id demo --history   # 跨进程看存档
python -m CharAgent.client --backend postgres --thread-id demo --resume    # 跨进程续跑
```

**四个 marker** 与用例的对应关系：

| marker | 覆盖 | 隔离方式 |
|--------|------|---------|
| `pg` / `redis` | checkpoint 的 PG / Redis 实现 | 直接连真服务；跑完把该会话的行删掉 |
| `pg_db` | db 层的仓储与 alembic 迁移 | 在**独立 schema**（`charagent_test` / `charagent_alembic_test`）里干活，跑完整个 schema 删掉 |

连不上真服务时按 conftest 的提示**跳过并说明原因**（那是在报「本机没起服务」，不是用例失败）。
checkpoint 的**默认**用例走内存版或 `FakeRedisClient`（`tests/doubles.py`，时钟可注入 ——
TTL 到期用「快进时钟」验，不真等）；db 层的默认用例则完全不碰数据库（形状 / 状态机 /
分层 / 映射 / 配置都能离线测）。
