# 08 · 框架侧：工具执行前的拦截点

**Status:** done

**Type:** task

**Blocked by:** 无

**上游:** `../PRD.md` §4.6 / §4.9（L2）、`CharApp/docs/PLAN.md` §5、`CharAgent/docs/DESIGN.md` #25（HITL 审批的前一半）

## 做什么

给框架加一个「工具执行前」的挂载点，并让它的返回值**有语义**：任一插件返回「拒绝 + 原因」→ 工具**不执行**，那条原因当作工具的执行结果回填给模型。

框架至今只有**执行完之后**的通知（`on_tool_executed`），所以权限校验、危险操作拦截、下单前审批全都无处可挂。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 框架现有 5 个 hook 点：`before_turn` / `after_turn` / `on_model_call` / `on_tool_executed` / `on_event`，**没有**执行前的拦截 | `hooks/utils/types.py:26-37` |
| `HookRegistry.fire()` 返回 `None`，且**异常隔离**（hook 抛异常只记入 `failures` 然后继续跑其余 hook） | `hooks/registry.py:117-138` |
| `loop.py` 是**唯一**持有 `self._hooks` 的地方 | `agent/loop.py:247` |
| `tool/executor.py` 的 `execute_tool(tool, *, arguments)` 是个**纯函数**，没有 registry | `tool/executor.py:179` |
| 同一 assistant 消息的多条 tool_call 走 `_execute_parallel` → `_execute_one(call)` | `agent/loop.py:277-305`、`:571` |
| 框架**已经保证**「工具错误永不抛异常、会转成模型看得懂的文字回填」 | issue 04 的验收；`agent/utils/events.py` |
| **`agent/loop.py` 在 L1 全程零改动**，是 issue 01 / 03 / 04 写进验收的核心证据 | 那三份 ticket 的验收行 |
| 事件顺序是先 sink 后 hook，所以 sink 回调里看到的顺序是权威的 | `stream/bus.py:140` |
| `EventSink = Callable[[StreamEvent], Awaitable[None] \| None]`；`StreamEvent` 是 `@dataclass(slots=True)`，`data` 是自由字典 | `stream/utils/types.py:51-75` |
| 取消时 `CancelledError`（`BaseException`）**直接传播**，插件不得挡住 kill switch | `hooks/registry.py:40-41`、`:122` |

## 具体任务

1. **新增 hook 点** `HookPoint.BEFORE_TOOL_EXECUTE`，载荷 `turn` + `call`（`ModelToolCall`）+ 该轮已注册的工具清单（插件判「这是不是写操作」需要它 —— 实现时确认载荷到底要什么，宁可多给一个只读引用也不要让插件去别处找）。

2. **`HookRegistry` 新增 `decide(point, **kwargs) -> Decision`** —— **不给 `fire()` 加返回值**。
   - 语义：任一插件拒绝 → 拒绝（原因取**第一个**拒绝者的）；全部放行 / 无人注册 → 放行。多条拒绝时其余原因怎么处置（丢弃 / 收集进事件）实现时定。
   - **异常策略要在实现时拍板并写进 docstring**：`fire()` 的策略是「插件出错不拖垮用户任务」；拦截点若照抄，一个坏掉的护栏 = 一道不存在的护栏。倾向 **fail closed（记入 `failures` 并拒绝）**，理由是拦截点是安全语义的 —— 但这是本片最需要想清楚的一条，别默认。

3. **`Tool` 加 `annotations`**：一个可选映射（如 `{"writes": True}`）。框架**只透传不解释** —— 与 `RunContext.payload` 同一条纪律：框架不知道 `writes` 是什么意思。

4. **`agent/loop.py` 在 `_execute_one` 里接线**：调 `execute_tool` 之前 `await self._hooks.decide(...)`；被拒时构造一个**失败态的 `ToolExecution`**（走现成的「工具错误」通道，事件序列与历史回填的形状一字不变）。

5. **改写「`loop.py` 零改动」的口径**（见备注第一条）。

6. **根门面防漂移测试与通用性测试跟着走**：`tests/test_root_facade.py` 的包名单、`tests/test_agent_provider.py` 的业务词扫描范围，都要明确新名字算哪一类。

7. **写测试。**

## 验收

- [x] 插件拒绝 → 工具函数**一次都没被调用**（断言调用计数为 0）+ 模型收到那条拒绝原因 + 该 `tool_result` 是失败态 + 事件序列只多不少、`seq` 连续
- [x] 全部放行 → 与从前**逐字一样**（用 `tests/snapshots.py` 的 `project_events` 对比）
- [x] **无人注册 → 零开销**：与从前逐字一样，且不产生额外的 await 链
- [x] 插件抛异常 → 行为明确（按第 2 条的拍板结果）且被一条用例钉住
- [x] **拦截不挡取消**：对齐 `hooks/registry.py` 已有的那条纪律（`CancelledError` 直接传播），用例从真取消路径触发
- [x] `Tool.annotations` **不被框架解释**：通用性测试守（框架源码里搜不到 `writes` 这类业务语义）
- [x] 框架现有测试全绿（809 条）
- [x] 用**框架自己的玩具业务**（与 issue 01 / 04 同一套做法）起一次完整运行，拦截效果在真事件序列上可见

## 备注

- **「`loop.py` 零改动」这面旗怎么改口径 —— 本片最容易被误读的一条。**
  L1 那句承诺的原文是「若加一个**业务接入点**需要改循环核心，说明接缝设计失败」。L2 改 `loop.py` 是为了加一个**框架自己的能力**（拦截点），**不是为业务破例** —— 本片全程不会在 `loop.py` 里写下一行 `minimall`。这两件事必须在 ticket、在 `loop.py` 的改动注释、在 L1 那三份 ticket 的回指处都分清，否则半年后读到会以为 L1 的验收被破了。

- **为什么不给 `fire()` 加返回值**：「值不值得拦截」是**只有某些 hook 点才有的性质**。在一个方法上附加「某些点的返回值算数、某些点的不算」的隐式规则，是以后一定会踩的坑；用两个方法把这个区别写进 API 形状里更牢。代价是多一个方法名 —— 这个代价比隐式规则便宜。

- **本片只做「放行 / 拒绝」两值，不要把「需要确认」提前塞进来。** L3 的 HITL 是「返回需要确认 → checkpoint 存档 → 用户确认 → `resume()` 续跑」，机制完全不同（挂起不是拒绝）。框架的快照机制里恢复路径已经写好了（`session.resume()` 的 docstring 点明了"工具调用还没有结果的半路"这个挂起点），L3 只需补「拦截 + 挂起」那一半。**本片若顺手把第三种返回值做进来，就会在没有真实调用方的情况下发明契约。**

- **拦截只发生在工具执行前，不改参数、不能包裹**（PRD §4.6 明确否掉了完整中间件链）。插件能做的只有一件事：说「不许」。

- 本片**不碰** `client/` 与 `server/`：`server/` 的只读会话历史端点是 13 的活；`client/` 的 `InteractiveRepl` 与 CLI 不受影响。

---

## 实际开发情况 2026-09-21

**一句话**：拦截线切穿了（新 hook 点 + `decide` + `Tool.annotations` + loop 接线），
新增 **24 条用例**（框架 809 → **833**，零回归），`ruff check` / `ruff format --check` 干净。
`agent/loop.py` 本片**动了**（+12 行功能代码 + 注释与载荷线程化）—— 动的是框架自己的能力，
不是为业务破例，下面第一节先把这条口径说清。

### 一、「`loop.py` 零改动」这面旗：改口径，不破旗

L1 那三份 ticket（01 / 03 / 04）的原文是「若加一个**业务接入点**需要改循环核心，说明接缝设计失败」。
本片改 `loop.py` 加的是**框架自己的能力**（工具执行前的挂载点），与那面旗说的不是一回事：

| | 业务接入点（L1 证过的） | 框架能力（本片加的） |
|---|---|---|
| 是什么 | 这次运行拿哪些工具 / 用哪份提示词 / 谁在问 | 循环本身多一个**可挂载的时机** |
| 反例 | 换业务要改主循环 = 接缝设计失败 | 加 `before_turn` 也要动 `_decide`；加拦截点要动 `_execute_one` |
| 判据 | 通用性用例：同一套装配装两个业务 | 通用性用例：框架源码里搜不到业务词（本片绿） |

**可执行的判据**：`loop.py` 至今没有、本片也没写进任何一行业务判断 —— 业务词扫描
（`tests/test_agent_provider.py::test_the_framework_never_mentions_the_business`）绿着，
说明这次改动对「换个业务要不要改框架」零影响。

三处口径已同步：本文件（这一节）· `agent/loop.py` 改动处的注释 · 01 / 03 / 04 的回指注记。

### 二、实现时拍板的开放项

| 开放项（初稿写「实现时定」的） | 结论 | 理由 |
|---|---|---|
| **裁决插件抛异常**（本片最需要想清楚的一条） | **fail closed**：按拒绝处理 + 记入 `failures` | 观察点的插件坏了顶多少一条日志；拦截点的插件坏了等于那道护栏**不存在**，而用户以为它在（PRD §1 批评的正是「没人用过的接口 = 没设计过」的孪生兄弟：没人发现坏掉的护栏） |
| 认不出的返回值（不是 `Decision` 也不是 `None`） | 同 fail closed，并造一条 `HookError` 记进 `failures` | 一个写错 `return "拒绝"` 的护栏若被当成放行，就是**静默失效** —— 与上一条同一个病 |
| 多条拒绝的原因 | 取注册顺序上**第一个**拒绝者的，其余丢弃 | 「第一个说不的说了算」一条规则；不收集是因为没有消费方（收集起来只是白存）。坏插件排前面时框架文案胜出 —— 有意为之：那道护栏的故障要**显出来**，不能被后面插件的具体原因盖住 |
| 载荷带什么 | `turn` + `call` + **`tool`**（不给「本轮的整份工具清单」） | 初稿想的清单是给插件「判这是不是写操作」用的，而这件事**只有当前这个调用**的 `Tool` 说了算（`annotations` 在它身上）。给整份清单反而诱导插件按名字去列表里再找一遍 —— 那是「让插件去别处找」。**修正**（代码审查指出，初稿这里写错过）：`before_turn` 的 `tools` 是 wire 规格（`to_spec()` 的结果），里面**不含** `annotations` —— 想看「全部工具里哪些是写操作」今天拿不到。按「没有真实调用方就不发明契约」的口径不补这个字段（issue 12 的护栏只用得上当前这一个调用）；真出现全量视角的调用方（如 L4 的工具裁剪）时再加 |
| 未知工具（模型幻觉） | **不进**裁决点，直接给可操作错误 | 那道门管的是「真实存在但这次不许跑」；不存在的工具本来就跑不了，没有可拦的东西。副产品：裁决插件拿到的 `tool` 必有值，不必判空 |
| `Tool.annotations` 怎么打 | 装饰器参数 `@tool(annotations={...})`（顺带做映射形状校验） | 只加字段而不给写法，业务只能在注册后手动改属性（比装饰器写法更容易写错）。框架仍然只透传：**不进 `to_spec()`**，模型看不见这一层 |
| `HookFn` 的返回值类型 | 合并成一个别名（返回 `Decision \| None`） | 类型上不为「观察点返回 None、裁决点返回 Decision」造三个别名 —— 返回值算不算数**由点决定、不由函数签名决定**（同一个函数可以挂两类点），这个区别写进 `fire` / `decide` 两个方法的形状里 |

### 三、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `hooks/utils/types.py` | `HookPoint.BEFORE_TOOL_EXECUTE`（插在 `ON_TOOL_EXECUTED` 前）；新增 `Decision`（放行 / 拒绝 + 原因，拒绝不给原因 = 构造期 `HookConfigError`）；`HookFn` 返回值改为 `Decision \| None` |
| `hooks/registry.py` | 新增 `decide()`；抽出 `_call()` 共用「插件异常只记一笔、CancelledError 不挡」这条纪律；`INTERCEPT_FAILED_REASON`（框架文案）；模块 docstring 补「两条异常策略为什么不同」 |
| `hooks/__init__.py` / `hooks/utils/__init__.py` | 门面导出 `Decision`；补一段业务拦截的用法示例（示例键用 `locked`，不是 `writes` —— 框架文档里不出现业务的注解词，那条扫描用例守得住） |
| `hooks/utils/errors.py` | `HookError` / `HookConfigError` 的措辞按新用途收敛（插件写错这类编程错误，含运行期发现的那种） |
| `tool/decorator.py` | `Tool.annotations` 字段 + `@tool(annotations=...)` + 映射形状校验（`dict(...)` 存一份，工具自己持有）+ `to_spec()` 不带它 |
| `agent/loop.py` | `_execute_one` / `_execute_parallel` 带 `turn`；`execute_tool` 之前 `await decide(...)`，被拒则构造失败态 `ToolExecution`（走现成的工具错误通道）；六处「五个触发点」的口径改成六个 |
| `agent/provider.py` | 一句「五个时刻」→ 六个 |
| `__init__.py`（根门面） | 导出 `Decision`（防漂移用例要求子包 `__all__` 的名字都在根门面上）；hooks 那行表格改成「六个触发点，工具执行前那个可拒绝」 |
| `tests/test_hooks.py` | 点集 5 → 6；新增 9 条：空注册不挂起（事件循环探针）· 不表态 / 明确放行 · 第一个拒绝者定原因（且不短路）· 抛异常 fail closed · 认不出的返回值 fail closed · 坏插件不中断其余 · `CancelledError` 不吞 · `fire` 忽略返回值 · 拒绝必须给原因 |
| `tests/test_loop_events.py` | 新增 8 条：被拒工具零调用 + 原因回填 + 事件形状不变 · 全部放行逐字段一样 · 无人注册逐字一样 · 载荷三件套（turn / call / tool.annotations）· 未知工具不打扰插件 · 护栏坏了 fail closed（run 照常收尾）· 取消不被挡住（真 `task.cancel()` 路径）· **玩具业务完整一跑**（写预算护栏 3 次，第 4 次被拦） |
| `tests/test_tool_decorator.py` | 新增 5 条：默认空 · 键值原样 · 注册后改传入的 dict 不影响工具 · 不进 `to_spec` · 非映射报错 |
| `tests/test_agent_provider.py` | 新增 2 条：`ANNOTATION_WORDS` 扫描（框架源码里搜不到 `writes`）· 注解不透明性（带函数值的注解跑完整问答不炸、模型侧看不到） |

### 四、验收逐条

| 验收 | 结果 | 证据 |
|------|------|------|
| 拒绝 → 零调用 + 原因回填 + 失败态 + seq 连续 | ✅ | `test_a_rejected_call_never_reaches_the_tool`（调用计数 `[]`；tool 消息内容 = 插件给的原因；`status=error` 且无 `summary`；`seq == [1,2,3]`）；顺带断言 `on_tool_executed` **照常**为被拒的调用产出失败态 `ToolExecution`（观测插件看得见每一次被拦下的调用） |
| 全部放行 → 逐字一样 | ✅ | `test_an_allowing_plugin_changes_the_event_stream_in_no_way`（两遍同脚本，`project_events` 逐字段比对 + 答复 / outcome / 轮数一致） |
| 无人注册 → 零开销 | ✅ | 「逐字一样」：`test_no_registration_is_exactly_the_old_behavior`；「不产生额外 await 链」：`test_decide_on_empty_registry_allows_without_suspending` —— 用 `loop.call_soon` 排的探针断言 `await decide()` **没有**把控制权交回事件循环（这条只测得准在注册表层，loop 层用的是同一个 `decide`） |
| 插件抛异常 → 行为明确 | ✅ | 拍板为 fail closed；注册表层 `test_decide_fails_closed_when_a_plugin_raises`（拒绝 + `failures` 里躺着 `RuntimeError`），loop 层 `test_a_broken_guardrail_fails_closed_at_the_loop`（工具没跑、模型收到框架文案、run 照常 FINISHED） |
| 拦截不挡取消 | ✅ | 注册表层 `test_decide_does_not_swallow_cancelled_error`；loop 层 `test_interception_never_blocks_the_kill_switch`（插件卡在 `gate.wait()`，`task.cancel()` 后 `CancelledError` 即时传播 + 无后台任务泄漏，与 `test_loop_guard.py` 同款断言） |
| `annotations` 不被框架解释 | ✅ | 静态：`test_the_framework_never_mentions_annotation_keys`（扫框架源码，`writes` 零命中）；行为：`test_the_framework_never_reads_inside_the_annotations`（注解里塞一个 **lambda**，走完整问答不炸，且模型的 tools 与 wire 历史里都搜不到；与既有的 `payload` 不透明性用例同一条纪律） |
| 框架现有测试全绿 | ✅ | `833 passed, 65 deselected`（基线 809，本片 +24，零回归）；`ruff check` / `ruff format --check` 干净 |
| 玩具业务完整一跑 | ✅ | `test_a_write_budget_guardrail_stops_the_last_call`：业务侧的「没听说过的」玩具业务（账本，工具带 `writes` 注解）+ 一条「写操作最多 3 次」的护栏插件，4 次调用 → 真事件序列 `ok / ok / ok / error`，写函数总共只跑了 3 次，模型收到拒绝原因后继续作答 |

### 五、留给下一片的一处（本片不碰 `client/`，写下来免得丢）

**`ChatSession` 收不了 hooks**：`client/session.py:109` 的构造参数里没有它。issue 12 要把护栏插件
装到会话上（它自己立的旗：「装配只有一处：CLI 与 server 都要装上」），得先给 `ChatSession` 加一个
`hooks=` 透传给 `AgentLoop`；否则业务只能绕开会话自己建 `AgentLoop`，那面旗当场就破。本片按 ticket
的要求没碰 `client/`，所以这是 12 的前置小事（一行参数 + 一行透传）。

### 六、代码审查改了什么（两轴各起了一个 sub-agent，都是真跑出来的）

| 审查发现 | 处理 |
|---------|------|
| **违反 88 字符行宽**（我新写的代码）：一行 `register(...)` 带 `# type: ignore` 有 93 字符 —— ruff 对带 pragma 的行不报 E501，工具抓不到 | 修：换行。顺手全量核了一遍本片**新增的每一行**字符数（`git diff -U0` + 逐行计长），现在 0 行超 |
| **重复代码**（我新写的）：`decide` 里「插件坏了 → 按拒绝」那条赋值写了两遍 | 修：抽出 `_verdict()` 把「一次调用的结果 → 裁决」翻成一处，循环体只剩三行；顺手删掉只做一次 append 的 `_record_failure`（审查指它是 Middle Man） |
| **重复代码**（我新写的）：`test_agent_provider.py` 两条扫描用例把循环体与「扫空即报错」逐字抄了两遍 | 修：抽 `_scan_for_words(words)`，两条用例各剩一行断言 |
| **重复代码**（我新写的）：`test_loop_events.py` 同一份 echo 脚本抄了三处（含一处既有的） | 修：提 `ECHO_SCRIPT` 常量（`ScriptedModel` 会拷一份列表，共用安全），三处都改用它 |
| **重复代码**（我新写的）：`test_hooks.py` 里同一个 `broken` 插件定义了两遍 | 修：提成模块级 `_broken` |
| **文档说错了一件事**（Spec 轴）：§二 那条「需要全量视角可以到 `before_turn` 拿工具清单」不成立 —— 那里的 `tools` 是 wire 规格，不含 `annotations` | 修：表格里改写成事实 + 说明为什么不补字段（见 §二） |
| **验收口径偏松**（Spec 轴）：`「不产生额外的 await 链」` 严格说不成立 —— loop 每次工具调用多一次**同步返回**的 await，能证的只是「不挂起」 | 修：把说法改准（`test_no_registration_is_exactly_the_old_behavior` 的 docstring + 本节）：没有回调、没有调度、不挂起，与别的 hook 点（fire）一致；「与从前逐字一样」另有一条**已提交的快照**（`tests/fixtures/snapshots/event_stream_tool_path.json`）原样通过作背书 |
| **词汇漂移**：同一个东西在框架文档里有三个叫法（拦截点 / 裁决类点 / 拦截插件），且 `HookError` 文案写成「裁决插件」 | 修：定名 —— 点是**拦截点**（类别叫**裁决类点**），挂在它上面的插件叫**拦截插件**，`decide` 这个动作叫**裁决**；全库统一 |
| `Decision(allowed=True, reason="x")` 没人管（docstring 说「放行时为 None」，代码不校验） | 修：`__post_init__` 两个方向都校验 + 用例各钉一条 |
| `INTERCEPT_FAILED_REASON` 是公开名却没进门面（`Decision` 进了），看着不对称 | **不动**：与既有 `tool/utils/messages.py` 的 `INTERNAL_ERROR_TEXT` 同款 —— 模块内公开、不进包门面（它是 `decide` 的实现细节，不是插件作者的 API） |
| `_call` 这个名字没体现「异常不外抛」（建议 `_call_isolated`） | **不动**：名字说的是「调用一个 hook」，隔离写在 docstring 第一行与调用点注释里；`_call_isolated` 读着像名词 |
| `turn` 一路透传到 `_execute_one` 只为拼载荷（Tramp data） | **不动**：这道载荷现在确实只需要它；等载荷长到三项以上再考虑打包成一个类型（review 自己也说「today fine」） |
| `@tool(annotations=...)` 参数 + 形状校验 + `dict()` 拷一份，比初稿「`Tool` 加 `annotations`」多 | **保留并记录**：不给写法，业务只能在注册后手动改属性（更容易写错）；这三处都服务 issue 12 的实际用法 |

**跑过的测试**：`CharAgent` 833 全绿（`python -m pytest -q`，本片新增 24 条）；跑过的单文件：
`test_hooks.py` / `test_loop_events.py` / `test_tool_decorator.py` / `test_agent_provider.py`；
`ruff check` / `ruff format --check` 干净。
