# 13 · 展示层：工具事件去字段化 + 会话历史

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 12（严格说只有后半段依赖它，见备注第一条）

**上游:** `../PRD.md` §4.9（L2）、`CharApp/docs/adr/0003`

## 做什么

两件让**浏览器看到的东西**变得正确的事：

1. **工具事件去字段化** —— `tool_call` / `tool_result` 的 `arguments` / `summary` / `error` 换成人话 `label`，**敏感数据不出 CharApp 进程**（ADR-0003）
2. **会话历史** —— 刷新页面 / 换标签页之后，对话还在

## 第一件：去字段化

落点是**业务侧包装 `event_sink`**（三层取舍见 ADR-0003）：

- 业务本来就持有 sink：框架的 `SessionProvider.provide(context, *, event_sink)` 把事件出口交给业务，业务再递给 `ChatSession`。**业务站在唯一出口上，包一层就是脱敏** —— 框架与 Django BFF 一行不改
- 在包装层里换掉 `data`：`{"label": "正在查询订单"}` / `{"label": "订单获取成功"}`
- **保留** `tool_name` + `tool_call_id` / `duration_ms` / `turn`（ADR-0003 的表格逐字列了哪些改哪些不改）
- **不动 `reasoning`** —— 那是模型自己的话，遮掉它会让 L3 的「当时它看到了什么」不可解释
- **不留演示开关** —— 「演示时把敏感数据打开」的环境变量是安全反模式
- 标签表放业务侧（`{tool_name: 中文短语}`），**17 个工具都要有**，且**未命中的工具要有兜底话术**，不能漏出一个英文工具名

## 第二件：会话历史

- **框架侧**：`CharAgent/server/` 新增**只读**历史端点。会话在 `SessionRegistry` 里按 `thread_id` 长驻，只差一个读口 + 一条路由
- **BFF**：`/minimall/agent/history/` 转发（身份仍**只从 session 取**，与 issue 06 同一条纪律）
- **前端**：`agent.html` 页面加载时拉一次，把历史渲染出来

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 事件载荷形状：`tool_call` = `{tool_call_id, tool_name, arguments, status, turn}`；`tool_result` = `{tool_call_id, tool_name, status, duration_ms, turn, summary\|error}` | `agent/utils/events.py:64-94` |
| **`StreamEvent` 是 `@dataclass(slots=True)`，不冻结，`data` 是自由字典 —— 可原地改写** | `stream/utils/types.py:51-69` |
| 业务侧本来就持有 sink | `CharAgent/server/utils/types.py` 的 `SessionProvider.provide(context, *, event_sink)`（`:133` 起） |
| `tool_result` 的 `summary` 是工具返回值**截断到 200 字符**的正文（订单号、地址、余额都在里面） | `stream/utils/types.py:46-47`、`agent/utils/events.py:59-61` |
| 框架 `server/` 现在只有 `POST /runs` 与 `POST /runs/{run_id}/cancel`，**没有**会话的 HTTP 路径 | `server/app.py:132`、`:176` |
| 会话按 `thread_id` 长驻在 `SessionRegistry`（`acquire` / `release`） | `server/sessions.py:144`、`:183` |
| BFF 的 `relay()` 现在逐帧**字节透传**，不改写 | `views_bff.py:651` |
| BFF 的身份只取 `request.user.pk`，不看请求体 | `views_bff.py:777`、`:831` |
| 页面现在刷新即新对话；`conversation_id` 每个标签页生成 | `templates/minimall/agent.html`、issue 06 的已知边界 |
| `thread_id = minimall:{user_id}:{conversation_id}`，框架对**整串**有 128 字符校验 | issue 06 的「计划外但必须做的」第 3 条 |

## 具体任务

1. **CharApp 侧包装 `event_sink`**（在 `service.py` 的装配处 —— **只有一处**，CLI 与 server 都要过）+ 标签表
2. **前端 `agent.html` 事件渲染改成读 `label`**（事件形状变了，不跟着改就是白屏）
3. **框架 `server/` 加只读历史端点** + 测试 + 根门面防漂移测试与通用性测试跟着走
4. **BFF 加历史转发端点**
5. **前端加载时拉历史并渲染**
6. 写测试

## 验收

- [ ] **浏览器 devtools 的网络面板里**，SSE 帧的 `data` 搜不到任何后端字段（订单号 / 地址 / 余额 / slug / 金额）
- [ ] 页面上工具行显示的是**中文短语**，且 17 个工具都有话说（没有兜底漏出英文名）
- [ ] `tool_name` 仍在事件里（演示时讲得出「模型选了哪个工具、有没有选错」）
- [ ] **刷新页面 → 对话还在**
- [ ] **换标签页 → 各自的历史各自恢复**（issue 06 用户故事 25 的延伸：各聊各的，现在还要各自记得住）
- [ ] **买家 A 拉不到买家 B 的历史**（身份从 session 取；请求体里塞别人的 `user_id` 无效）
- [ ] 历史端点**只读**：任何写意图（POST / DELETE）→ 405 或 404，且**不产生任何运行**
- [ ] 框架 / CharApp / 商城三套测试全绿

## 备注

- **想与 11 并行的话**：先做**框架历史端点那一半**（它不依赖任何 L2 前序片），去字段化等 12 的 17 个工具清单定稿再做 —— 否则标签表要维护两遍。
- **代价要说清**（ADR-0003 已记）：关掉了「在浏览器里看模型看到了什么」这个演示窗。它没有丢，只是**搬到了 L3 的轨迹落库** —— 那才是正确的排查入口（不受「谁能看到浏览器」约束，也不要求脱敏）。
- **脱敏必须在业务侧**：框架不该知道「什么算敏感」（那是业务知识）。这条与 `RunContext.payload` 同源 —— 框架只透传，不解释。
- **BFF 的 `relay()` 一行不改**：它是字节搬运，脱敏在更上游完成了。这是这条分层决定的主要收益 —— 如果要动 `relay()`，说明分层选错了。
- **历史端点只读，且不做「从第 N 号接着推」**（框架的 SSE 层明确不支持续推，见 `CharAgent/server/sse.py`）。刷新后是「重新拉一份完整历史，再开始新的一轮」，不是续流。
- **不做**：`reasoning` 的改写（ADR-0003 说了理由，归 L4 的 prompt 调优）、历史的分页（会话历史天然短）、历史的消息级删除。
