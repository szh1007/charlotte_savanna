# C10 · L5-c：逐句引用（事件 payload → BFF → 前端 → 历史）

**Status:** done

**Type:** feature

**Blocked by:** C09

**上游:** `.scratch/Charlotte/PLAN.md` §6.2 的 L5-D4；`CharApp/docs/adr/0003`（工具事件在业务侧脱敏）

## 现状（2026-09-30 读码，四条是这次设计的关键约束）

**① `final` 事件的载荷只有一个字符串。** `CharAgent/stream/utils/types.py:44` —— 终局集在 `:53-59`，而 `#4` 立的规矩是「**终局事件恰好一个**」（`DESIGN.md` 里那条不变量）。

**② 前端有恰好两个正文写入口，都是纯文本节点。**
- 直播：`finish(content)` → `turn.body.textContent = text`（`templates/minimall/agent.html:498-501`）
- 历史重建：`appendAnswer(handle, text)` → `handle.body.textContent = ...`（`:770-776`）

两处**都必须改**，只改一处会出现「刷新前有引用、刷新后没了」。另外 `el()`（`:362-369`）一律用 `textContent`，注释明写**不用 `innerHTML`**（防模型吐 HTML）—— 新代码必须守住这条。

**③ `/history` 返回的也是纯 `{role, content}`。**

**④ ADR-0003 挡住了最省事的那条路。** 最自然的做法是「前端从 `tool_result` 事件里拿结构化引用」，但你的 ADR-0003 规定工具参数与结果的**原文一个字节都不进浏览器**。

**现成的先例**：`approval_required` 事件 + `buildApprovalCard`（`:898-1066`）—— 它证明了「服务端给结构化字段 → 页面按字段渲染卡片」这套在这一页是成立的。**照它做。**

---

## 一、服务端：引用从哪来

在 `ON_TOOL_EXECUTED` 钩子（或记录层）里取本次运行中 `search_knowledge` 的**结构化结果**，按出现顺序编号 → 存给收尾时用。

**`final` 的载荷扩成**：

```json
{ "content": "……[1]……", "citations": [ {"n": 1, "title": "退货与退款政策", "slug": "refund-policy", "snippet": "…前 200 字…"} ] }
```

**两条边界要写清楚**：

- **编号以服务端为准**，模型写不出来的编号（编的、越界的）**原样当纯文本**显示 —— 不报错、不猜。
- **`snippet` 是服务端从检索回来的 chunk 原文里截的前 200 字**，不是模型生成的。它就是**答复所依据的那一段**。

**⚠️ 这是在 ADR-0003 上开的一个口子，必须显式记一条 ADR（或修订 0003）**：
- 原规则防的是「内部实现与原始数据泄漏」
- 修订后的边界：**被答复引用的那一段**可以进浏览器，且只给**被引用的 chunk 的前 N 字**，不给完整工具返回、不给参数原文
- 不写这条 ADR，半年后读代码的人会以为这是漏洞

**不新增事件类型** —— 引用是**终局答复的一部分**，不是过程事件。扩载荷不动 #4 那条「终局事件恰好一个」的不变量；新增一类事件则要动它，代价大得多。

---

## 二、BFF 与历史

- BFF 透传即可（`app/minimall/views_bff.py` 的 `TERMINAL_EVENTS` 在 `:282`）—— 但**要确认不透传时不会把 `citations` 吃掉**
- `/history` 的返回要带上引用。**优先方案：不加库列** —— `charagent_messages` 有 `run_id` 列，历史重建时按 `run_id` 去 `charagent_tool_calls` 里取当时的 `search_knowledge` 结果，**用与实时完全相同的编号算法重放**，于是两边一致
  - 这套成立的前提是「编号是确定性的」（按 tool_call 出现顺序 + chunk 主键顺序）
  - **开工时先验证**：若发现编号无法稳定重放（例如同一 run 多轮里工具名重复出现导致顺序歧义），退回「`charagent_messages` 加一列 `citations` JSONB」—— 那是框架层改动，要动 schema / alembic / 仓储 / 记录层四处，成本更高但要如实付

---

## 三、前端

`finish()` 与 `appendAnswer()` 两处从「写 `textContent`」改成「**按 `[n]` 切分**」：

- 用 `document.createElement('span')` + `textContent` 拼节点，**继续不用 `innerHTML`**
- `[n]` 渲染成可点的小标记；点开显示来源卡片（标题 + snippet），照 `buildApprovalCard` 的模式
- 映射不上的 `[n]` **原样当文本**

---

## 验收

- [x] 问政策题 → 答复里带 `[n]` → **点得开、显示的是检索到的原文片段** —— 真机（浏览器 + 真模型 + 真 Milvus）：问「退款要几天才能到账, 运费谁承担」→ 答复里 6 枚 `[n]` 小标记 → 点开 `[1]` 显示「📄 运费与配送说明」+ 语料正文（服务端截的前 200 字，句子在 200 字处截断）
- [x] 刷新页面 → **历史里的引用还在、还能点** —— 真机：刷新后 6 枚标记重建（走 `/history` 的 `citations`），点开 `[4]` 显示「📄 退款政策: 付款之后怎么退钱」。这一条把「两个写入口都改了」钉死（直播走 `finish`，历史走 `appendAnswer`，两处调用**同一个** `renderAnswer`）
- [x] **否定断言**：抓浏览器网络响应，工具的参数原文与完整返回**仍然不在里面** —— 真机 SSE 帧里 `tool_call` / `tool_result` 只有 `label`（「正在查订单列表」/「订单列表查到了」），没有 `arguments` 与返回正文；`/history` 里只有被引用那几段的 200 字摘录。用例 `test_the_tool_payload_never_reaches_the_browser_with_citations` 用两个只可能来自原文的串钉住（检索词、摘录之外的那一截）
- [x] 模型编了一个不存在的编号 → 原样当文本显示，页面不崩 —— 用例 `renderAnswer` 的判据 + `test_a_number_inside_the_body_is_not_a_new_block`（解析器不认不连续的号）。真机没法强制模型编号，这一条靠用例
- [x] 没有引用的一次运行（比如查订单）→ `citations` 为空数组或缺失，前端正常 —— 真机：问「我的订单到哪了」答复里零标记、页面照常；用例 `test_a_run_without_knowledge_has_an_empty_citation_list` 断空列表
- [x] 前端仍然不使用 `innerHTML` —— 模板里三处 `innerHTML` 全是注释（说明为什么不用），实际渲染一律 `createElement` + `textContent`
- [x] ADR 已记 —— 新开 `adr/0027`（「被引用的那一段可以进浏览器」），并在 `adr/0003` 里补一条指针说明这是**有名字的例外**

## 改了哪些文件

| 文件 | 说明 |
|---|---|
| `CharApp/minimall/knowledge/citations.py` | **新写**：引用契约 —— `Citations`（这段对话的引用账：领号 / 收原料 / 交付）、`citations_from_results`（**直播与历史共用的那一个**解析函数）、`citation_sink`（`final` 出门前挂引用） |
| `CharApp/minimall/history_citations.py` | **新写**：记录表 → 这段会话检索过的原文 / 每条消息该带的引用（`HistoryCitations` 是框架那个插座的业务实现） |
| `CharApp/minimall/knowledge/formatting.py` | `format_chunks(chunks, *, start=1)` —— 编号改成**这段对话的累加序号**（调用方给起始号） |
| `CharApp/minimall/tools.py` / `provider.py` / `service.py` | 检索工具领号（`citations.take`）；提供者多收一个引用账；装配处每会话造一份、`ON_TOOL_EXECUTED` 钩子收原料、出口裹上 `citation_sink` |
| `CharAgent/server/app.py` + `server/utils/types.py` | 框架的**第三个插座**（可选）：`create_app(message_extras=...)` —— 读历史时按行下标把业务字段并进消息（框架不认识它们的含义） |
| `CharApp/minimall/server.py` | `create_minimall_app` 把 `HistoryCitations` 交给那个插座（没配库时不装） |
| `CharApp/minimall/prompt/system/v6.prompt` + `manifest.yaml` | **新写**：v5 + 一条「要带编号」的规矩（C09 留着的那个开关，在这里打开）；`default: v6` |
| `templates/minimall/agent.html` | `renderAnswer`（按 `[n]` 切分 + 来源卡）+ `toggleSource`；`finish` / `appendAnswer` 两个写入口都改；CSS 两段 |
| `CharAgent/tests/test_server_history.py` | 四条新用例（按序合并 / 内存来源不问 / 插件出错不吞 / 别的路由不问） |
| `CharApp/tests/{test_citations,conftest,test_tools,test_provider,test_scoping,test_guardrail,test_prompt}.py` | 11 条新用例（解析 / 编号 / 出口 / **端到端：直播与历史逐字一致** / 否定断言 / 历史行）＋既有装配点补 `citations=` |
| `CharApp/eval/golden.py` | 造工具名清单时补一个引用账（与检索器同一个理由） |
| `CharApp/docs/adr/0027-*.md` + `adr/0003-*.md` | 新 ADR + 0003 的例外指针 |
| `sh/charapp_demo.sh` / `sh/charapp_demo.md` | 装置加第 5 步（知识库索引，幂等；Milvus 不在就跳过）；§六回填政策问答那一段（C04 早就写明由 L5 落地时写） |

## 实施记录

**引用从哪来：直播与历史共用一段文本、一个函数。** 直播那条路的原料由
`ON_TOOL_EXECUTED` 钩子收（`execution.content`），历史那条路读
`charagent_tool_calls.result` —— **同一段文本**（落库时一个字没改），喂给同一个
`citations_from_results`。于是"刷新之后引用还在"不是两套算法碰巧一致，而是同一份
输入走同一个函数；有一条端到端用例把两条路的结果**逐字**比对（不是比条数）。

**编号改成「这段对话的累加序号」（票面没写，实施时定的）。** 票面 §一 的例子是
`[1]…[5]` 一次调用一套号，而 §二 自己担心「同一 run 多轮里工具名重复出现导致顺序
歧义」—— 两次检索都从 `[1]` 起的话，模型写的 `[3]` 到底指哪一段就成了谜。改成累加
之后：同一个号在这段对话里只指一段（模型上下文里看到的也是同一套号），"服务端按号
回指"才成立。恢复旧会话时编号与引用都从记录表回读续上（进程重启不会从 `[1]` 重数）。

**历史用「回读落库文本」而不是加库列**（票面 §二 的优先方案，验证后成立）：解析的是
**我们自己写出去的格式**（`[n] 标题` + 正文，契约在 `formatting.py`），两条纪律守着
它 —— 解析要求编号**连续**才认新段（正文里出现 `[9] …` 不算段落边界），写与读两侧
各有用例。加库列要动 schema / alembic / 仓储 / 记录层四处，而落库那段文本**就是模型
当时看到的那一份**，拿它当唯一依据最不容易漂。

**引用数组里没有 `slug`**（票面 §一 的 `{n, title, slug, snippet}` 少一个）：历史那条
路手里只有那段文本，而它没写 slug —— 想在两边都带上，就得把 slug 塞进**模型看得到**
的文本里只为给前端用，不值。卡片要的"这是哪一条"由标题回答；要打开原文那天再加。

**框架多了一个可选插座**（`create_app(message_extras=...)`）：引用是业务的话术（谁被
引用、怎么编号、摘要多长），框架只做一次**按行下标的浅合并**、不认识那些字段。形状
与另两个插座同款（结构化协议，有 `provide` 就算）；只在读**记录表**那条路调它（内存
那份历史没有"行"可对），插件出错**不吞**（那是业务自己的读口）。

**提示词 v6**（票面没点名，但验收第 1 条「答复里带 `[n]`」要求它）：C09 那一版**故意**
不写引用规则（票面 @C09 ③ 明写"这个开关在 C10 打开"），所以 C10 必须把开关打开 —— 新
一版 `v6.prompt` 只加一条「要带编号：用查到的内容回答时，在每个说法后面写上它出自哪
一段，只写查到的编号、不要自己造」。`manifest.yaml` 切 `default: v6`；那条守着"关着"
的用例按当初的预告原地翻成"这一版要求了"（C09 写它时就写了"下一版打开时该改的是它"）。

**前端一处实现、两个调用点**：`renderAnswer(host, text, citations)` 被 `finish`
（直播）与 `appendAnswer`（历史重建）共用 —— 这两处**必须都改**，只改一处会出现
「刷新前有引用、刷新后没了」。渲染一律 `createElement` + `textContent`（`el()` 那条
纪律照旧）；来源卡同一时刻只开一张、再点收起（跟确认卡同一条规矩）；对不上引用的
`[n]` 原样当文本。

**真机踩到的三个坑**（都已写进代码注释）：
① 钩子载荷里的 `call` 是**模型发起的调用**（`ModelToolCall`，名字在 `.name` 上），
不是记录层那个 `ToolCall` 实体 —— 按 `.tool_name` 取会 AttributeError，而观察类钩子
的异常**被框架吞掉**（只记 failures），症状是"引用一条都没有"却不报错；
② 落库那一侧的状态可能是普通字符串，`call.status == ToolCallStatus.SUCCEEDED` 要用
**值比较**（`is` 在假库上直接 False）；
③ 新写的 `.prompt` 文件是 CRLF（Windows），而 v1–v5 都是 LF —— `diff` 一改就是整个
文件，`manifest` 那句「逐字可 diff」当场失真。写新版本文件后要转成 LF。

**交付前过一道机械过滤**（评审抓出来的第一版缺陷）：第一版把**检索到的全部段落**
都放进了载荷，而 ADR-0027 与票面写的边界是"**被引用的**那一段才进浏览器" —— 两处
对不上。改成：扫答复正文里出现了哪几个 `[n]`（`cited_numbers`），只带那几个；
"号指哪一段"仍然由服务端那份表说了算（不猜模型的意思）。历史那条路同一条判据。
否定断言据此加强：另一段（检索到但没被引用）的标题必须在响应里搜不到，而直播的
SSE 与 `/history` **两个响应都扫**（原来只扫了前者）。

**一处如实记的边界**：`citations` 是"这段会话到这一轮为止"那本账的**子集** ——
跨轮引用是允许的（模型上下文里一直有前面几轮的条文，它可以引 `[4]`），所以某条
答复的载荷里可能出现**上一轮**检索到的段。

**装置回填**（C04 的票面早就写明这一片落地时做）：`sh/charapp_demo.sh` 第 5 步跑一次
知识库索引（幂等，全量重建二十秒上下；Milvus 不在就跳过并说清后果），`sh/charapp_demo.md`
新增 §六「政策问答那一段」（4 步 + 每步的兜底）。跑装置实测：冷启动 41 秒（其中索引
占 13 秒左右）。

**评审后的修补**（两轴评审：标准 / 票面，各一个子代理并行跑；下面这些是改掉的，
其余逐条判断后保留）：

| 发现 | 处置 |
|---|---|
| **票面/ADR 说的边界是「被引用的那一段」，而第一版把检索到的**全部**段落都放进了载荷** | 交付前加一道**机械过滤**（`cited_numbers` 扫答复里出现的号，只带那几个）；历史那条路同一判据；ADR 与票面里「超集」那两段如实改写。否定断言据此加强：另一段（检索到但没引用）的标题必须搜不到 |
| 否定断言只扫了 `/runs` 的 SSE，没扫 `/history`（那也是浏览器响应） | 两个响应一起扫 |
| **装置第 5 步只跑索引、没跑 `seed_knowledge`**（干净库上会「成功」地建一个空索引，而装置报「就绪」） | 先 `seed_knowledge`（幂等）再建索引，两步共用一个 `if`；C08 票面交接的那一半就此落地 |
| 框架 `create_app` 的 Args 顺序与签名不一致（Google Style） | `message_extras` 挪到 `idempotency` 之后 |
| `MessageExtras` 的下标契约没写「投影逐条同序同长」这条前提 | 写进协议 docstring（并注明「投影哪天开始丢行，这里必须一起改」） |
| `citation_sink` 自称与 `redacting_sink`「同一套写法」，实际一个用 `isawaitable`、一个用 `is not None` | 改成真的一样（`inspect.isawaitable`） |
| `history_citations.provide(rows: Sequence[Any])` 比协议松；测试里一个死导入；`service.py` 一句注释把「读库失败」也说成会兜底 | 三处都改（类型收成 `Sequence[Message]`、删死导入、注释说准：读库失败照抛） |
| ADR 只写了「正文出现 `[9] …` 不算段落边界」，没写「正好是下一个号」那半边 | ADR 里补一句（并记当前语料 `^[` 出现 0 次，风险有界） |
| 票面 §一 的载荷形状少了 `slug`、§二 的「重放」前提与实现不同 | **保留**，理由：slug 在历史那条路上取不到（见实施记录），而"重放"实现成"回读文本里写着的编号" —— 那串编号**就是**当时算法算出来的结果，比重新算一遍更不容易漂（票面那句话的意图是"两边一致"，端到端用例逐字比对证明它成立） |

**过滤那条改动的真机复核**（真模型 + 真 Milvus，与浏览器同一个装配）：问「退款要几天
才能到账」→ 答复引用 `[2, 3, 5]`，载荷带的正是 `[2, 3, 5]`（检索回来 6 段，没引用的
那几段不在）；问「你们提供发票吗」→ 零引用，载荷是空数组。**浏览器那一次验收是在
过滤改动之前跑的** —— 渲染与历史那两条链路的证据仍然成立（过滤只减少数组里的条目），
而"载荷按引用过滤"这条由上面这次真机 + 端到端用例钉住。
