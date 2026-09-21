# 08 · 框架侧：工具执行前的拦截点

**Status:** ready-for-agent

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

- [ ] 插件拒绝 → 工具函数**一次都没被调用**（断言调用计数为 0）+ 模型收到那条拒绝原因 + 该 `tool_result` 是失败态 + 事件序列只多不少、`seq` 连续
- [ ] 全部放行 → 与从前**逐字一样**（用 `tests/snapshots.py` 的 `project_events` 对比）
- [ ] **无人注册 → 零开销**：与从前逐字一样，且不产生额外的 await 链
- [ ] 插件抛异常 → 行为明确（按第 2 条的拍板结果）且被一条用例钉住
- [ ] **拦截不挡取消**：对齐 `hooks/registry.py` 已有的那条纪律（`CancelledError` 直接传播），用例从真取消路径触发
- [ ] `Tool.annotations` **不被框架解释**：通用性测试守（框架源码里搜不到 `writes` 这类业务语义）
- [ ] 框架现有测试全绿（809 条）
- [ ] 用**框架自己的玩具业务**（与 issue 01 / 04 同一套做法）起一次完整运行，拦截效果在真事件序列上可见

## 备注

- **「`loop.py` 零改动」这面旗怎么改口径 —— 本片最容易被误读的一条。**
  L1 那句承诺的原文是「若加一个**业务接入点**需要改循环核心，说明接缝设计失败」。L2 改 `loop.py` 是为了加一个**框架自己的能力**（拦截点），**不是为业务破例** —— 本片全程不会在 `loop.py` 里写下一行 `minimall`。这两件事必须在 ticket、在 `loop.py` 的改动注释、在 L1 那三份 ticket 的回指处都分清，否则半年后读到会以为 L1 的验收被破了。

- **为什么不给 `fire()` 加返回值**：「值不值得拦截」是**只有某些 hook 点才有的性质**。在一个方法上附加「某些点的返回值算数、某些点的不算」的隐式规则，是以后一定会踩的坑；用两个方法把这个区别写进 API 形状里更牢。代价是多一个方法名 —— 这个代价比隐式规则便宜。

- **本片只做「放行 / 拒绝」两值，不要把「需要确认」提前塞进来。** L3 的 HITL 是「返回需要确认 → checkpoint 存档 → 用户确认 → `resume()` 续跑」，机制完全不同（挂起不是拒绝）。框架的快照机制里恢复路径已经写好了（`session.resume()` 的 docstring 点明了"工具调用还没有结果的半路"这个挂起点），L3 只需补「拦截 + 挂起」那一半。**本片若顺手把第三种返回值做进来，就会在没有真实调用方的情况下发明契约。**

- **拦截只发生在工具执行前，不改参数、不能包裹**（PRD §4.6 明确否掉了完整中间件链）。插件能做的只有一件事：说「不许」。

- 本片**不碰** `client/` 与 `server/`：`server/` 的只读会话历史端点是 13 的活；`client/` 的 `InteractiveRepl` 与 CLI 不受影响。
