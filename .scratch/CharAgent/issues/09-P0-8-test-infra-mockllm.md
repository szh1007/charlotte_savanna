# 09-P0-8 — MockLLM 三模式 + 轨迹断言 + 快照测试基建

**What to build:** 测试基建：MockLLM 实现 ChatModel 协议三模式（固定返回 / 脚本化序列 / 录制回放），被测代码零改动（#61）；确定性保证（temperature=0 + seed + 注入随机源）；轨迹断言工具（断言工具调用顺序与参数而非仅最终答案 #62）；快照/契约测试（checkpoint 序列化、事件流、双适配器契约 #63）。测试矩阵落地：并行工具、错误自纠错、循环防护、断点续跑、reasoning、HITL、幂等、取消、降级、限流边界等核心场景。

**Blocked by:** 01, 04, 07

**Status:** done

- [x] MockLLM 三模式完成，被测代码零改动（#61）
- [x] 确定性：temperature=0 + seed + 注入随机源，同输入同输出（#61）
- [x] 轨迹断言工具：工具调用顺序与参数断言（#62）
- [x] 快照测试：checkpoint 序列化 JSON 快照、SSE 事件序列快照、双适配器契约（#63）
- [x] 测试矩阵核心场景全部落地（并行/纠错/防护/续跑/reasoning/HITL/幂等/取消/降级/限流）—— P0 侧（并行 / 纠错 / 防护 / 续跑 / reasoning / 事件 / 幂等 / kill switch）全部有例；HITL 审批 / 降级 / 限流属 P1-7/P1-8/P1-5，生产代码未落地，经确认记为遗留（见 §8）
- [x] `pytest tests/` 全绿（全 mock，CI 默认）—— 621 passed / 65 deselected

## Comments

**2026-09-14 实施完成**（提交前双轴 code-review + 修复，见 §9）。落点全在 `CharAgent/tests/`：新增 5 个模块 + 4 个用例文件 + 两处 fixture，改 2 个既有测试文件（`mock_llm.py` 扩写、`doubles.py` 加替身）；**生产代码零改动**（`git status --short -- CharAgent/{agent,model,tool,stream,hooks,retry,checkpoint,db,alembic}` 为空，即 issue 第 1 条的字面实现）。默认全量 **621 passed / 65 deselected in 68s**，Ruff check + format 零告警。

### 1. 落点与分工

| 文件 | 职责 |
|------|------|
| `tests/mock_llm.py`（改） | `MockLLM` 三模式 + 样本读写（`LLMSample` / `LLMExchange`）+ 录制器 `RecordingChatModel`；`ScriptedModel` 保留为兼容别名 |
| `tests/trace_assertions.py`（新） | 轨迹断言 `Trace` / `trace_of`（#62）+ 确定性采样常量与 `assert_pinned_sampling`（#61） |
| `tests/snapshots.py`（新） | 快照落盘 / 比对（`assert_json_snapshot` / `assert_text_snapshot`）+ 两条归一投影（#63） |
| `tests/record_llm_samples.py`（新） | 真实 API 录制脚本（非 pytest 收集），同时导出「录制场景」供用例复用（工具 / 提问 / `make_loop`） |
| `tests/doubles.py`（改） | 新增 `EventCollector`（事件收集 sink）—— 快照与载荷断言需要一个共享取数口 |
| `tests/fixtures/llm/*.json`（新） | 三份**真实录制**样本：`text_stop` / `tool_path` / `tool_path_thinking` |
| `tests/fixtures/snapshots/*.json`（新） | 两份快照：事件序列 `event_stream_tool_path`、落盘帧 `checkpoint_frames_tool_path` |

### 2. MockLLM 三模式（第 1 条 / #61）

| 模式 | 构造 | 行为 |
|------|------|------|
| 固定返回 | `MockLLM.fixed(resp)` | 恒返回同一条，**不判耗尽**（单测分支用） |
| 脚本化序列 | `MockLLM.scripted([...])` | 按调用次数依次弹，弹空即报错并指出第几次被调用 |
| 录制回放 | `MockLLM.replay("tool_path")` | 逐轮回放样本里的响应原文 |

- **兼容面**：`ScriptedModel = MockLLM`（issue 04 起的那批测试文件零改动）；空脚本**允许构造**（有一批校验用例只需要一个模型实例把 loop 建起来，从不 generate），真被调用时给可读报错
- **两半记录**：`calls`（模型每轮看到什么，messages 走浅拷贝冻结调用时刻）+ `responses`（模型每轮决定了什么）。两半都要 —— 工具调用只在响应里：最后一轮被刹车拦下的调用不会出现在任何一次请求的 messages 里，只记请求就断言不全
- 三种模式都不改被测代码：实现的是 `ChatModel` 协议（薄协议，不要求继承），loop / retry 包装层拿到的东西与真适配器同型

### 3. 录制与回放（第 1 条的录制面）

- **录制**：`python tests/record_llm_samples.py`（打真实端点、消耗额度）。本次录 3 份样本共 5 次调用（`deepseek-flash`，temperature=0 + 固定 seed，2026-09-14T15:01Z）：单轮文本 / 两轮工具链路 / 两轮思考模式
- **回放走生产解析**：样本存的是**响应原文**，回放时经 `model/parse.py` 的 `parse_chat_completion` 重新解析 —— 「解析层对真实响应读得对不对」因此也一起被回归，而不是回放一份别人算好的结论；样本畸形在构造期就炸
- **`verify_requests=True`**（契约回归开关）：回放时比对「本次请求」与「录制时的请求」（messages + tools），任何 wire 漂移都红。`test_mock_llm.py` 里最重的一条用它跑完整工具链路；`test_replay_contract.py` 用它验证**思考模式的 reasoning 回填与录制时逐字一致**
- **体检通过才落盘**：录完先体检（工具轮确实调了工具 / 工具结果确实回填 / 思考样本至少一轮带思维链），不合格只打印问题 + 退出码 1，**不动仓库里那份好样本**。体检判据复用轨迹断言（`assert_tool_result_backfilled`），规则只定义一处
- 实测两个上游脾性写进了注释：收尾轮常常不带 `reasoning_content`（与 issue 07 §9.A 记的偶发同源，故体检只要求「至少一轮」）；思考模式的工具轮正文是空串。另外 Windows 控制台 GBK 打印会被模型答案里的 emoji 打断，脚本显式切 UTF-8（`errors=replace`）

### 4. 确定性（第 2 条 / #61）

三处注入缝，全部落在既有注入点上（不改生产代码）：

| 面 | 做法 |
|----|------|
| 采样 | `trace_assertions.pinned_sampling()` = temperature=0 + `PINNED_SEED`；`Trace.assert_pinned_sampling()` 断每轮都带上（漏 seed 会被发现） |
| 时钟 | `doubles.FakeClock` 注入 `LoopGuard.time_source`（耗时字段确定） |
| 随机 | `doubles.FixedRandom` 注入 `RetryPolicy.random_source`（退避序列确定，issue 06 已有例） |

用例 `test_same_input_same_output_with_pinned_sampling`：同一输入跑两次，事件流（归一后逐字段）+ 轨迹文本必须完全相同。**限定**照官方语义写进注释：思考模式下 temperature 不生效、`top_p` 下限 0.95、seed 仅保证 content 可复现 —— 需要逐字复现的用例一律走非思考模式。

### 5. 轨迹断言（第 2 条 / #62）

```python
trace = trace_of(model)
trace.assert_tool_calls([("query_order", {"order_no": "20260701123456"}), "refund"])
trace.assert_turn_count(2)
trace.assert_tool_result_backfilled("query_order", contains="已发货")
```

- 期望项：字符串断工具名；`(名字, 参数字典)` 断**参数子集**（JSON 里多出的键不较真）；参数是畸形 JSON 时给出「无法按参数断言」并附原文
- 失败信息统一版式：标题 + 期望序列 + 实际序列 + 分轮轨迹，不让人对着断言猜哪里不对
- `exact=False` 可断「期望是实际序列的前缀」
- **自测含反路**（`test_trace.py`，19 例）：顺序错 / 参数错 / 条数错 / 畸形 JSON / 没回填 / 最后一轮才调 / 未知工具 / 漏 seed，逐条验证「必须红」—— 断言帮手假绿会让一批用例一起假绿

### 6. 快照测试（第 4 条 / #63）

| 对象 | 落点 | 说明 |
|------|------|------|
| 事件流 | `snapshots/event_stream_tool_path.json` | 真实样本回放跑出的 `thinking → tool_call → tool_result → final`，逐字段载荷 |
| checkpoint 序列化 | `snapshots/checkpoint_frames_tool_path.json` | loop 每轮**真正落盘**那两帧（`encode_record` 的产物），含 `schema_version` / 进度 / 观察值 |
| 契约 | `test_replay_contract.py` | 三条解析路径对同一份真实 wire 响应等价：回放 / httpx 裸调 / openai SDK（判等沿用 `test_model_contract.assert_same_response`，同一把尺子） |

- 三条约定：**首次运行生成并失败**（新快照等于没有防线，得有人看过一眼）；`UPDATE_SNAPSHOTS=1` 覆盖（有意改形状的书面记录）；**每次都不一样的东西不许进快照** —— `project_events` / `normalize_checkpoint_record` 把耗时换 `<ms>`、uuid 换 `<id>`、时刻换 `<ts>`，`run_id` 则在用例侧钉成常量
- 两条归一投影各有自测，含「没有父帧时 `parent_id` 保持 None」这类必须留住的语义边界

### 7. 测试矩阵现状

| 场景 | 现状 |
|------|------|
| 单工具 / 并行工具 / 错误自纠错 / 循环防护三种触发点 / 断点续跑 / time-travel / reasoning / 事件序列 / 幂等 / kill switch | ✅ 既有用例（issue 03~07）；本次补的是**基建**与真实样本侧的回归 |
| 取消（#18） | loop 层 kill switch 已有例；**run 状态机与 server 侧取消**属 P1-2 |
| HITL（#25） | checkpoint 挂起落盘与补做已有例（issue 07）；**审批 / 拒绝 / 超时**属 P1-7 |
| 降级（#19） | ❌ 属 P1-8，无生产代码 |
| 限流（#22） | ❌ 属 P1-5，无生产代码 |

### 8. 遗留（不属本 issue）

- **HITL 审批 / 降级 / 限流**：生产代码分别在 P1-7 / P1-8 / P1-5。经用户 2026-09-14 确认：**不写 skip 占位用例**（占位会污染「全绿」的口径），落地时接本次基建（同一 `ChatModel` seam）。已写进 `docs/design/04-test-plan.md` §3「尚未落地」表
- **非确定性统计**（#62 的「多次运行统计通过率而非单次通过」）：默认用例全替身、本身确定，没有可统计的对象；真实端点侧的重复试验归 P2-8 评估体系（同表已记）
- **流式（SSE）样本回放**：三份样本都是非流式。流式 delta 累积已有 respx 合成样本覆盖（issue 01/02 契约测试）；真实 SSE 原文回放留到 P1 SSE 落地时按同一样本格式补
- **并行工具的「真并发」样本**：并行语义已有脚本化用例验证（issue 04 用时间戳断言非串行）；真实样本里模型只发了一次单工具调用（录制提问没触发并行），要补只需改录制提问重录
- 样本重录需要额度（一轮 ≈ 5 次调用）；重录命令与 `UPDATE_SNAPSHOTS=1` 都写进了 `04-test-plan.md` §6

### 9. 双轴 code-review 与处置（提交前）

**Standards 轴**（仓库规范 + Fowler smell 基线）：

- 修：`doubles.py` module docstring 与本文件新增替身自相矛盾（「四个替身」未更新、把带 `__call__` 的 `EventCollector` 归进「不是可调用对象」、标题未含事件收集）→ 重写清单并说明它注入的是事件出口
- 修：`EventCollector` 里本次无调用点的 `types` / `of` / `to_dicts` 删除（YAGNI；需要时两行加回）
- 修：`test_mock_llm.py` 函数内 import 上移到模块顶部（与同批文件一致）
- 未采纳：`assert_same_response` 从 `test_model_contract.py` 上移 `helpers.py` —— 搬动要改既有契约测试文件，收益只是位置；已在 import 处写明「同一把尺子，不做两份」
- 未采纳：请求记录由裸 dict 升为 dataclass —— 那个 dict 就是**样本文件格式与 wire 形状本身**，引入类型要两边转换；构造已收敛在 `request_record()` 一处
- 未采纳：`USER_MSG` / `echo_tool` 在两三个文件里同形（未达项目 DRY 阈值「重复 ≥3 次」，且各自独立可读；`echo_tool` 已收敛为每文件一个工厂函数）

**Spec 轴**（issue + PRD + `difficulties/12-testing.md`）：

- 修：**录制脚本先落盘后体检** —— 与脚本自述「录歪了不如不录」相悖（录歪会把仓库里那份好样本覆盖掉）。改为体检通过才 `_save`，并补两条用例钉住时序（临时注入旧行为验证过用例真有牙：旧行为下必红）
- 修：`sample_path("tool_path.json")` 被当相对路径（带后缀的名字走不到样本目录）→ 只给名字时统一进样本目录，带目录的原样使用
- 修：FIXED 模式手工构造空脚本会抛 `IndexError`（绕过可读报错）→ 走同一条 `_exhausted_message`
- 修：`04-test-plan.md` 两处口径夸大/缺失 —— 「注入随机源」改为写清三处注入缝各是什么；补「非确定性统计未落地」一行
- 修（cleanup）：三处 replay 装配收敛到录制场景的 `make_loop`（并让它透传 `event_sink` / `saver`）——「loop 长什么样」只有一处定义，录制与回放不会各写一份

### 10. 验收证据

- **默认全量**（全替身、零外部依赖）：`621 passed / 65 deselected in 68.25s`；本次新增 **60 例**（`test_mock_llm.py` 23 · `test_trace.py` 19 · `test_snapshots.py` 10 · `test_replay_contract.py` 8）
- **Ruff**：`ruff check tests/` + `ruff format --check tests/` 零告警
- **真实端点**：录制脚本跑通并产出 3 份样本（5 次调用，`deepseek-flash`），样本体检全过；`test_replay_contract.py` 用这批真实样本在**零网络**下回归解析层与双适配器一致性
- **生产代码零改动**：`git status --short -- CharAgent/{agent,model,tool,stream,hooks,retry,checkpoint,db,alembic}` 为空 → 未触碰 agent 层，按 issue 07 惯例不必补跑 `-m integration`；模型层的真实端点行为已由本轮录制覆盖
- **快照确定性**：`UPDATE_SNAPSHOTS=1` 覆盖后快照字节不变（无偶发字段残留）

### 11. 与 issue 10 的衔接

`pytest tests/` 已是 issue 10（CLI 验收）的前置条件之一；CLI 演示还可以直接吃这批样本做**离线冒烟**（`MockLLM.replay(...)` + 同一 `make_loop`），不必每次打真实端点。
