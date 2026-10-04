# C29 · 增量渲染-d：业务开关 + ADR-0029 + 真机 TTFT 验收

**Status:** done（两条补做当天晚上都补完了：正式开关下的浏览器整轮复验 + 答复口径的归因戳
—— 见验收与实施记录；节流那条也在同一天定案）

**Type:** feature

**Blocked by:** C28

**上游:** C26–C28 三片；本批共同定案见 C26 那张表（Q7 开关 / Q9 验收 / Q18 ADR）。

## 现状（读码核过，带行号）

- `MinimallService` 是 **frozen dataclass**（`CharApp/minimall/service.py:299-370`），两处构造点：
  服务进程 `CharApp/minimall/server.py:300-322`（`build_service`）、命令行 `CharApp/minimall/cli.py:278-291`
  （`service_for`）；`session_for`（`service.py:392-512`）把字段透传给 `ChatSession` 与模型。
- 跑分自建实例：`CharApp/eval/harness.py:238-247` —— **新字段必须有默认值**，否则跑分装置要跟着改。
- 事件的消费口三处都不需要改：BFF 逐帧透传（`app/minimall/views_bff.py:1170`，`x-accel-buffering: no`
  挂在 `:1548-1551`），`read=120s` 是**相邻两次读取的间隔**不是整条流的总时长（`:217`）。
- `CONTEXT.md` 的「增量 / 增量渲染」词条已在 grilling 时落好（本票核对它与实现一致，不重写）。

## 本票要做的四件事

### 1. 开关：`MinimallService.stream` + `CHARAPP_STREAM`

```python
@dataclass(frozen=True, slots=True)
class MinimallService:
    ...
    # 答复是否走增量渲染 (C26-C28)。**字段默认 False**: 打开它会让一次运行多出成百条
    # 事件, 而 ~80 处既有的事件序列断言吃的是这里的默认值 —— 只有服务入口显式打开。
    stream: bool = False
```

- 服务入口按 `.env` 读 `CHARAPP_STREAM`（默认 `on`），与 `CHARAPP_THINKING` 同一套写法；
  **CLI / 跑分 / 用例吃字段默认值**（共同定案 Q7）。
- **录制回放那条线必须关**：`RecordingChatModel` 要求非流式的 `response.raw`（`mock_llm.py:353-358`），
  而流式路径从不设它（`model/stream.py:149-170`）—— 这句写进 `.env.example` 与工厂的 docstring，
  免得以后重录样本时莫名其妙地炸。

### 2. 透传：`session_for` → `ChatSession` → 模型

- `ChatSession` 需要把 `stream` 传到 `generate(...)`（loop 侧 `agent/loop.py:933-942` 是唯一的调用点）。
  **注意**：loop 侧的开关 **C27 已经做好**了 —— `AgentLoop(stream=False)` 就是这个字段，关着时
  一个关键字都不多传（两个回调都不挂），开着时才带 `stream=True` + 两个回调。本票要做的只是
  **把业务开关接到它身上**：`MinimallService.stream` → `session_for` → `ChatSession.__init__` →
  `AgentLoop(stream=...)`。
- **C28 真机验收时用过一次临时开关**：当时直接改 `CharAgent/client/session.py` 里那处
  `AgentLoop(..., stream=True)` 验的（验完已还原 —— 工作区里不留那次改动）。本票把这条正式化。

### 3. ADR：`CharApp/docs/adr/0029-…`

- 主题：**吐过字不重试**（Q11-D）。要写全四条的对照（A 前端按遍数覆盖 / B 服务端扣住不发 /
  C 流式一律不重试 / D 吐过字才不重试）与各自的代价，并与 `ADR-0025`（熔断在重试之内）**互指** ——
  它们讲的是一件事的两面。
- 符合开 ADR 的三条判据：难反转（改回来要动重试策略）、不看会疑惑（「这里为什么不重试」）、真权衡。
- **另外半条不进 ADR**：「`delta` 只作预览、`final` 是权威值」DESIGN #4 已经写过，本票只引用。

### 4. 真机验收（三条硬判据）

| # | 判据 | 怎么量 |
|---|---|---|
| 1 | **首字时间（TTFT）** | 同一个问题（「退款要几天才能到账」）在开关前后各问一次；服务端帧时间戳（`answer_delta` 的第一帧）与提问时刻之差，与手记对照 |
| 2 | **增量拼接 == `final.content`** | C27 的用例已钉；真机再抽一次答复人工比对 |
| 3 | **总时长不劣化** | 同题前后各一次的总耗时（`final` 到达时刻 − 提问时刻） |

真机记录写进下面的「实施记录」，并回填 `sh/charapp_demo.md`（若演示话术要提"逐字"）。

## 改了哪些文件（计划）

| 文件 | 改动 |
|---|---|
| `CharApp/minimall/service.py` | `stream: bool = False` 字段 + docstring |
| `CharApp/minimall/config.py` | `ENV_STREAM = "CHARAPP_STREAM"` + 读取函数（与 thinking 同族） |
| `CharApp/minimall/server.py` | `build_service` 读 `CHARAPP_STREAM`（默认 on）传给字段 |
| `CharAgent/client/session.py` / `agent/loop.py` | `stream` 透传到 `generate(...)` |
| `CharApp/docs/adr/0029-*.md` | **新写**（吐过字不重试） |
| `.env.example` | `CHARAPP_STREAM` 一行 + 录制回放必须关的那句 |
| `sh/charapp_demo.md` | 演示话术按需补一句（逐字那一段） |

## 验收

- [x] 真机三条判据（TTFT 前后对比 / 拼接一致 / 总时长不劣化），数字写进实施记录
      —— 两个实例（`:1008` 默认开 / `:1009` `CHARAPP_STREAM=0`）打同一句话，逐帧记时间戳：
      长答复题**首字 1.38s vs 15.99s**；退款题首字 8.21s vs 9.80s（那几秒都花在检索上）。
      拼接一致：成功观测到的每一次都逐字相等；**一次不等**已定位到设计内的那条来路
      （工具轮叙述，见实施记录）
- [x] 服务端那条链用**正式开关**验过：`CHARAPP_STREAM` → `MinimallService.stream` →
      `session_for` → `ChatSession` → `AgentLoop(stream=...)` → `answer_delta` 帧
      （`:1008` 那份就是正式代码 + 正式默认值）
- [x] 浏览器整轮演示走通（逐字 → 工具轮搬移 → 引用可点 → 刷新整段）—— **C28 已做**
      （那次用的是临时开关，前端代码与今天逐字相同）
- [x] **补做已完成（2026-10-05 晚）**：**重启装置**（新进程 = 正式代码 + `CHARAPP_STREAM` 默认 on）
      后在浏览器里走完整轮 —— 两问的帧级记录见实施记录；`answer_delta` 的**归因戳**也拿到了
      （首块 = 工具轮叙述，工具结果之后的首块 = 答复正文）
- [x] **跑分不受影响**：`MinimallService.stream` 的字段默认 `False`，跑分装置
      （`CharApp/eval/harness.py:238`）构造时根本不传它 → 每轮把整段历史发出去的旧形状
      一字不变；机械证据是 `CharApp/tests` **439 passed**
- [x] **CLI 不受影响**：`python -m CharApp.minimall.cli --user-id 10 -q "退货政策是什么"`
      输出与今天一致（一条 `[reasoning]` 整段 + `[final]`，**没有** `[answer_delta]` 行；
      走的是字段默认值 False）
- [x] ADR-0029 落盘（`CharApp/docs/adr/0029-once-a-delta-is-out-the-request-is-not-resent.md`）；
      `.env.example` 里 `CHARAPP_STREAM` 与 `CharApp/CONTEXT.md` 的「增量 / 增量渲染」
      词条对得上（词条那句 `_Avoid_：拿「流式」指这个效果` 正是这次的口径）
- [x] `pytest CharAgent/tests` **1522 passed** + `pytest CharApp/tests` **439 passed**；
      `manage.py test app.minimall --noinput` —— 本票没动 Django 侧一行，那把全量留给
      用户跑（本批次唯一动过的 Django 文件 `app/minimall/tests/test_bff.py` 单独跑过：
      **135 条 OK**）

## 边界与不做

- **不做帧率节流** —— **已量到数据并定案（2026-10-05）**，见下面「收尾记录：节流闭环」段。
- **不做"遍数覆盖"**（Q11 的 A 方案）：D 实施之后它用不上，留作将来。
- **不做移动端 / 其它入口的开关**：只有服务入口开。
- **不动 `EventPrinter` 的逐字**：CLI 这一批不做（C27 只补了一行事件渲染）。

## 实施记录

（2026-10-05 实施。约束：开关默认关在**零件**上、默认开在**服务入口**上 —— 口径与票面一致。）

**落点**

| 文件 | 改动 |
|---|---|
| `CharApp/minimall/config.py` | `ENV_STREAM` + `stream_from_env`（两态：不填 = 开；写坏当场报启动期错误） |
| `CharApp/minimall/service.py` | `stream: bool = False` 字段（docstring 写清"为什么字段默认关"）+ `session_for` 里透传 |
| `CharApp/minimall/server.py` | `build_service` 读 `CHARAPP_STREAM`（显式打开） |
| `CharAgent/client/session.py` | `ChatSession(..., stream=False)` → `AgentLoop(stream=...)` |
| `CharApp/docs/adr/0029-*.md` | **新写**（吐过字不重发：四条备选对照 + 与 ADR-0025 互指 + 边界） |
| `.env.example` | `CHARAPP_STREAM` 一行 + 「录制回放必须关掉它」那句 |
| `sh/charapp_demo.md` | 新增**第八节**（逐字那一段的看点、真机数字、兜底三条） |
| `CharApp/tests/test_server.py` | 开关两态 + env→零件 + 零件→事件三条用例（各做过变异验证） |

**为什么开关是"字段默认关、入口默认开"**：`MinimallService.stream` 的默认值决定的是
**用例与跑分**看到的形状（~80 处事件序列断言 + 跑分快照），`CHARAPP_STREAM` 决定的是
**用户**看到的形状。两者方向相反是**有意的**：新产品行为在服务入口显式打开，其余
入口（CLI / 跑分 / 用例）保持旧形状。

**真机三条判据（2026-10-05）**

做法：起两个实例（`:1008` 用默认 = 开；`:1009` `CHARAPP_STREAM=0`），对同一句话打
`POST /runs`（每次换一个新会话号 —— 复用会话时第二次的模型看得见第一次的答案，会跳过
检索，那就不是同一个问题的两次测量了），逐帧记时间戳（脚本：`D:\__WorkSpace__\Temp\c29_ttft_check.py`）。

| 题 | 实例 | 首字可见 | final | 增量块数 | 帧型 |
|---|---|---|---|---|---|
| 退款要几天才能到账（先检索再答） | `:1008` 开 | **8.21s** | 8.80s | 151 | reasoning 70 + tool 2 + answer_delta 151 |
| 同上 | `:1009` 关 | **9.80s**（= final） | 9.80s | 0 | reasoning 1 + tool 2 |
| 同上（第二趟） | `:1008` 开 | **8.97s** | 9.47s | 121 | 同上形 |
| 同上（第二趟） | `:1009` 关 | **8.48s**（= final） | 8.48s | 0 | 同上形 |
| 「用六条要点讲一遍退款规则」（长答复） | `:1008` 开 | **1.38s** | 14.46s | 443 | reasoning + tool 2 + answer_delta 443 |
| 同上 | `:1009` 关 | **15.99s**（= final） | 15.99s | 0 | reasoning 1 + tool 2 |

**读法（评审后收紧了口径，两条分开读）**：

- **「答复区开始出字」**（用户先看见东西的那一刻）：长答复题 **1.4s vs 16.0s**；
  检索题 8.2 vs 9.8（第二趟 9.0 vs 8.5 —— 那一题的两趟差值落在模型速度的正常波动里，
  别拿单趟说事：**它的几秒都花在检索上**，逐字省下的是"生成"那一段）。
- **「最终答复的字开始出现」**：**这一条这次没测出来** —— 长答复那趟第一块字是
  工具轮叙述（随后被搬进过程行），而脚本没记每块文本，分不开"叙述"与"答复"。
  `c29_ttft_check.py` 已补上两个戳（**首块文本**与**工具结果之后的首块时间**），
  下次真机直接读得出；连同浏览器复验一起列进验收的补做项。
- **总时长**：没有劣化（两边的 final 时刻都落在模型生成速度的波动里，13–16s）。

**拼接一致（判据 2）**：成功观测到的每一次都逐字相等（含 244 字 / 151 块那次）。
**有一次不等**（长答复题，443 块 / 708 字）—— 已定位到设计内的来路：那一趟模型在
调工具**之前**说了一段话，那段的增量也发了（用户在等工具时看得见它），而
`content_parts.clear()` 会把它剔出最终答复（归属由随后的 `thinking` 事件说明，
前端把它搬进过程行）。离线用 MockLLM 复现了这条机制（有叙述时全程拼接多出叙述那一段，
去掉它逐字相等）。所以本批的不变量口径收紧为：**「增量拼接 == final.content」按轮成立**，
三条设计内的不等来路是工具轮叙述 / CONDENSE 丢弃前缀 / 截断放弃时压根没有 final
（C26 那张共同定案的表已按这个口径改写）。

**浏览器整轮复验（2026-10-05 晚，正式开关下的新进程；帧级记录）**

方法：页面里装一层 `fetch` 钩子抓原始 SSE 帧（时刻 + 类型 + 文本），另挂 `PerformanceObserver`
数 `longtask`。两问：

| 问 | 帧型 | 首块 | 工具结果后的首块 | final | 增量帧 | 长任务 |
|---|---|---|---|---|---|---|
| 查订单（带「先说一句你准备做什么」） | reasoning 54 + answer_delta 101 + thinking 1 + tool 2 + final 1 | 201ms（2 字 = 叙述） | **1025ms** | 1329ms | 101（约 11ms/块 ≈ 90 块/秒） | 0 |
| 退货政策是什么 | reasoning 70 + answer_delta 340 + thinking 2 + tool 1 + final 1 | 129ms（14 字 = 叙述 `这个我帮你查一下商城的规定。`） | **9848ms**（检索等了 9.7s） | 10958ms | 340（约 3ms/块 ≈ **310 块/秒**峰值） | 0 |

- **搬移两问都成立**：叙述先出现在答复区，随后进过程行（`思路 …` + `操作 …` 两行），
  最终答复区没有它；刷新后 `agent-delta` 残留 **0 个**，8 枚引用仍可点、来源卡打得开。
- **拼接对账（第二问，逐字）**：全程增量 = 叙述 14 字 + 答复 564 字 = 578；`final.content` = 564
  —— 与"按轮成立"的口径逐字吻合（第一问同理：叙述 2 字）。
- 控制台 **0 error / 0 warning**。
- **长任务两条都是 0** —— 这条是「要不要做帧率节流」那份分析的实测输入（见下条）。

**环境事故（记一笔，不是代码问题）**：测到一半（约 01:35 起）模型 API 连不上
（`ModelConnectionError`，:1007/:1008/:1009 三个实例一起失败；`api.deepseek.com` 本身
可达，401），剩下的"浏览器用正式开关再走一遍"没做成 —— 见验收里那条补做。

**评审发现与处置**

| 发现 | 处置 |
|---|---|
| `build_service` 的接线用例原写 `CHARAPP_STREAM=off` 再断言 `False` —— 字段默认就是 `False`，"没接上"也过得去（**非判别**） | 改成显式 `on` 断言 `True`，并做变异验证（拆掉 `stream=stream_from_env()` 会红） |
| `stream_from_env` 是同文件**第三份**逐字相同的 bool 解析（`thinking_from_env` / `_context_bool`） | 抽出 `_bool_from_env(values, name, default, note=...)`，`stream_from_env` 与 `_context_bool` 都委托它；`note` 只进错误消息的括注（"不填 = 开" vs "不填 = 用默认值"） |
| 新注释放在实参**之后**，与两处调用点的既有惯例（注释在实参前）不符 | `server.py` / `service.py` 各上移一行 |
| ADR-0029 的替代方案表把**被采纳的 D** 也列进"为什么否掉"表 | 改成"四条里 D 就是决定，下表是被否掉的三条 + 两条边角做法"，并补一行"让业务侧决定"的否掉理由 |
| ADR-0029 的「落地」没列本票新增的业务侧开关与 `.env.example` | 补上（config / service / server / session / `.env.example`） |
| 票面要求与 `ADR-0025` **互指**，但 0025 那边没有回指 | 0025 开头补一段"与 0029 互指" |
| 票面要求录制那句也进**工厂的 docstring** | `CharAgent/client/app.py` 的 `build_model` 加 Note（`.env.example` 那份早就有） |
| 判据 1 的归因：长答复那趟 1.4s 的第一块字**可能是工具轮叙述**（那次拼接不等正说明有叙述），脚本没记每块文本 | 脚本补两个戳（首块文本 / 工具结果后首块时间）；票面把"答复区开始出字"与"最终答复的字开始出现"**分开读**，后者标为待补 |
| 判据 3 的措辞（检索题第二趟反超，单趟差值是噪声） | 票面写明"那一题别拿单趟说事；它的几秒花在检索上" |
| 「真机 100ms 级」这句与数据不符（一块几毫秒） | 演示文档改成"逐块、一块几毫秒；244 字 151 块" |

**收尾记录：节流闭环（2026-10-05 定案）**

共同定案「粒度」那条留的口子是"留一个节流口，等真机量到帧率再决定要不要合并" —— 现在量到了，
结论是**不做**，理由与触发线都写在这里（下次有人问"为什么 300 帧/秒不打帧"照这段答）：

- **数据**：上游一块 1–2 字、间隔 p50 = **0ms**、p90 = 22ms，峰值 **~310 块/秒**穿过 BFF 到浏览器；
  一条 564 字的答复共 **340 块**。三处代价都是**每帧固定开销**（字节可忽略）：服务端一次
  `bus.emit` → 一次 sink 写；BFF 一次切帧 + 抠头 + 一次 yield；浏览器一次 `appendData` + 一次
  `toBottom()` —— 都随**并发会话数线性增长**。
- **没病的证据**：浏览器 `longtask` 两条都是 **0**；本机 BFF / 服务无压力；单用户演示形态。
- **不做的主要理由**：要动就得动**这一批四片的验收口径**（"一块一帧 / 逐块对账 / harness 23 条"），
  换来的是一半看不见的帧。
- **触发线（满足任一条就先做「服务端合帧」）**：① 同时 **3 个以上**会话在答；② 走远端 / 隧道部署
  （每帧多一跳网络）；③ 低端设备 / 移动端。验的时候看三个数：**帧数/秒、BFF CPU、浏览器长任务**
  （本次那份 `fetch` 钩子 + `PerformanceObserver` 脚本直接能用）。
- **口子在哪**：`forward_deltas`（`model/stream.py`）是唯一转发点、`_delta_emitter`（`agent/loop.py`）
  是唯一埋点处 —— 合帧只改一处，且**首块必须立即发**（不然就把 TTFT 那点收益又赔回去）。

**没做的事（按票面边界）**：不做帧率节流（上面已定案）、不做"遍数覆盖"、不动 `EventPrinter` 的逐字、
不多开入口的开关。**未提交**（等指令）。
