# 10-P0-acceptance — CLI 演示（P0 验收线）

**What to build:** `python -m CharAgent.client` 命令行入口：用真实 DeepSeek 跑通带工具问答（模型决策 → 工具执行 → 结果回填 → 最终答案），终端实时展示事件流（thinking/tool_call/tool_result/final）；checkpoint 中断续跑演示（Ctrl-C 中断 → 重跑从 checkpoint 恢复，不重复已完成动作）；配置切换 checkpoint 存储（InMemory/Redis/Postgres）。这是 P0 阶段整体验收线。

**Blocked by:** 04, 05, 07, 09

**Status:** done

- [x] CLI 入口：带工具问答端到端跑通（真实 API）
- [x] **接上重试包装**（issue 06 的接线点）：构造 loop 时用 `RetryingChatModel` 包一层裸适配器 —— `AgentLoop(model=RetryingChatModel(chat_model_from_env(), policy=RetryPolicy(...)), tools=..., event_sink=...)`。issue 06 交付的 `retry/` 设计为组合在 **ChatModel 协议层**（`ChatModel` 是 Protocol，不要求继承），故 loop 与 `model/` 零改动、类型注解照写 `ChatModel`；**在接这一行之前，`retry/` 没有任何生产调用点**（只有测试在用）
- [x] 终端事件流展示（四类事件 + reasoning）
- [x] checkpoint 中断续跑演示：中断 → 恢复不重复已完成动作（#5）
- [x] checkpoint 存储配置切换可演示（InMemory/Redis/Postgres，ADR-0002）
- [x] `pytest tests/` 全绿（P0 验收：带工具的 agent loop 跑通 + 断点续跑 + mock 单测）

## Comments

**2026-09-15 实施完成**（提交后双轴 code-review + 修复，见 §10）。落点 `CharAgent/client/`（新包）：`app.py`（参数解析 + 装配 + 常驻事件循环 + REPL）/ `session.py`（ChatSession）/ `render.py`（终端渲染）/ `utils/`（commands 交互命令解析 + types 启动选项）。

**生产代码侧另有三处改动**（都不动既有逻辑，只改对象形状与名字）：`CharAgent/__init__.py` 补全根门面导出、`checkpoint` 与 `retry` 的 `__all__` 各补一个常量名、`db.__all__` 去掉两个配置内部常量（§10-D）。**另有一次跨 7 个生产文件的环境变量改名**（§10-D），发生在初次交付之后 —— 它比前两处大，别按「小改」理解。

### 1. 交付清单

| 文件 | 职责 |
|------|------|
| `client/__init__.py` | 门面（对外公共 API） |
| `client/__main__.py` | `python -m CharAgent.client` 的模块入口（三行） |
| `client/app.py` | 进程入口：参数解析 → 装配（模型 / 存储 / 会话）→ 常驻事件循环 → 交互与一次性两条路；Ctrl-C 打断（`_KillSwitch`）也在这 |
| `client/session.py` | `ChatSession`：问一句 / 接着跑 / 看存档 三个动作 + `DEMO_TOOLS` |
| `client/render.py` | `EventPrinter`（EventSink 协议）+ 结果摘要 / 答复正文 / 取值口径 |
| `client/utils/commands.py` | 交互命令（`/resume` `/history` `/help` `/quit` + 别名）的枚举与解析纯函数 |
| `client/utils/types.py` | `CliOptions`（不可变启动工单）+ 默认会话编号 |

### 2. 为什么这算「验收线」而不只是「写个 demo」

issue 06 §9 记过一件事：`retry/` 交付后**生产调用点为零**（`RetryingChatModel(` 除 docstring 外全在测试里），`AgentLoop` 也是（`grep "AgentLoop("` 只命中 docstring 与测试）。也就是说 P0 前八个 issue 的成果**只在自己的单测里自洽**，从没被生产代码拼起来过。

CLI 是第一个真实调用点。它一旦跑通，说明这些协议（`ChatModel` / `Tool` / `CheckpointSaver` / `EventSink`）拼得起来 —— 这正是 ticket 把 CLI 定为验收线的原因。装配形状（与 ADR-0001 / ADR-0002 一致）：

```
ChatSession
  ├── model:  RetryingChatModel(chat_model_from_env())   <- 重试包在协议层 (issue 06 的接线点)
  ├── tools:  DEMO_TOOLS (P0-2 的五个演示工具)
  ├── saver:  checkpoint_saver_from_env() / build_saver()
  └── loop:   AgentLoop(model, tools, saver=..., thread_id=..., event_sink=...)
```

### 3. Ctrl-C 打断：`_KillSwitch` 与常驻事件循环（ticket 第 3 条）

进程结构刻意**不是**「一次 `asyncio.run` 跑到底」：

- REPL 是同步的（要用 `input()`），会话是异步的 → 本模块持有一个**常驻事件循环**，每次「跑一件事」用 `_KillSwitch.run` 把协程丢进去跑完。常驻不只是省事：httpx 的连接池绑定创建它的那个循环，每次换新循环会让第二次请求踩到「连接属于别的循环」的坑（实测确认）。
- `_KillSwitch` 收到 `KeyboardInterrupt` 时：`task.cancel()` → **再跑一次循环等它收尾** → 才把中断抛给上层。不等收尾就退出会留下「任务没结束但循环已停」的悬空状态（CPython 会报 `Task was destroyed but it is pending`）。
- 落盘快照的时机因此是安全的：被打断的那一轮虽然没跑完，但**上一轮结束时的帧早已存下** —— 这就是「恢复而非重跑」的前提。

实测（2026-09-15）：`python -m CharAgent.client` 跑起来问一个要调工具的问题，第一轮工具执行完屏幕出现 `tool_result`，按 Ctrl-C → 打印「`[中断] 本次运行已取消 (kill switch). 快照: PostgresCheckpointSaver 里 1 帧`」→ `/resume` → 直接出 `final`（**没有再出现 tool_call**）→ `2 轮`。

### 4. 五条验收的实测证据（真实端点）

| ticket 条目 | 实测 | 证据 |
|------------|------|------|
| 带工具问答端到端 | ✅ | `-q "我的订单 20260701123456 到哪了"` → `tool_call` → `tool_result` → `final`，退出码 0 |
| 并行工具 + reasoning + thinking | ✅ | `-q "把 3.5 公里换算成英里, 顺便告诉我现在 UTC 时间"` → 一轮里两条 `tool_call`（并行）+ `[reasoning]` + `[thinking]` + 两条 `tool_result` + `final` |
| 重试包装接线 | ✅ | `build_model` 里那一行；重试发生时 `on_retry` 打「`[retry] 第 N 次尝试失败, Xs 后重试: 原因`」 |
| 中断续跑不重复已完成动作 | ✅ | 见 §3 实测；`tool_call` 全程只出现 **1 次** |
| 存储配置切换 | ✅ | 同一条命令分别用默认（内存）与 `--backend postgres` 跑通；`--history` 跨进程打印了两帧的对齐表格（帧/轮次/来源/本轮tok/本轮ms/工具/结束/挂起/父帧） |

### 5. 顺手补的 P0 遗漏（issue 01~09 全量复核的结果）

ticket 第 3 条要求「之前遗漏的所有属于 P0 的问题都需要闭环」。逐条读 issue 01~09 的 Comments / 遗留 / 未采纳章节并去代码里核实后，闭环了这些：

| # | 遗漏 | 出处 |
|---|------|------|
| 1 | 根门面只导出 model + tool，缺 agent / stream / hooks / retry / checkpoint / db | issue 05 §9 · 06 §9 · 07 §8 · 08 §5 **四处**都写「同批处理」 |
| 2 | `.env.example` 零 `CHECKPOINT_*` / `MODELS_*`（演示者无处得知变量名） | 本轮审计；后按用户要求把这 9 个变量统一加 `CHARAGENT_` 前缀（`MODELS_*` 词干改为 `DB_*`），详见 §10-D |
| 3 | `checkpoint/utils/history.py` 的 `format_history` 零生产调用点（issue 07 自己立过「不给没人调的接口」的规矩） | CLI 的 `/history` 与 `--history` 用上了 |
| 4 | `DESIGN.md` 目录结构无 `client/` 行；`05-roadmap.md` 落点列全是旧单文件名；`04-test-plan.md` 的 E2E 行承诺了不存在的 REST/TaskQueue | 本轮审计（E2E 那行已拆成「P0 已有（CLI）」与「P1 规划」） |
| 5 | issue 03 / 08 的 `Status:` 仍是 `ready-for-agent`（bullet 全 `[x]`） | 本轮审计 |
| 6 | `PRD.md` 「当前状态」写「代码零实现，P0 尚未开工」 | 本轮审计 |

**加了两条防漂移用例**（比再写一遍待办管用）：
- `tests/test_root_facade.py` —— 子包 `__all__` 里的每个名字，根门面必须有且是同一对象；九层之间不许同名。
- `tests/test_env_template.py` —— 拿 `checkpoint.config` / `db` 里声明的常量名去比对 `.env.example`，模板漏写就红。

### 6. 落盘中实测到的一个真 bug（已修）

**标准输入不是 UTF-8**：默认编码 GBK + `errors=surrogateescape`，于是**管道喂进来的 UTF-8 中文**被解成乱码加一串孤立代理项（`'\udcad'`），一路带进请求体，最后在 httpx 的 `json.dumps` 处炸成 `UnicodeEncodeError: surrogates not allowed` —— 报错位置离真正的原因（输入编码）十万八千里。交互式在 Git Bash 里敲中文同样中招（pty 发的是 UTF-8 字节）。

修法：`_use_utf8_stdio()` 把 **stdin 与 stdout 都**切成 UTF-8 + `errors=replace`（原 `_use_utf8_stdout` 只管一半）。Windows 控制台那一路也安全 —— 控制台底层是 UTF-16，这里只是中间那层往返，换成 UTF-8 只会更不容易丢字符（GBK 连 emoji 都编不回去）。回归用例 `test_piped_utf8_chinese_survives_the_encoding_setup` 起子进程验真实编码行为（pytest 会把 `sys.stdin` 换掉，进程内断言等于测了个假的）。

### 7. 设计取舍（附理由）

- **`final` 事件只印一行摘要，正文由调用方从 `LoopResult.content` 打印**：03-api.md §2.4 定案 delta 只作渐进预览、`content` 才是权威值（CONTINUE 截断续写时它是跨段拼合结果）。两处都印会重复一遍答案。
- **交互命令只四条**（`/resume` `/history` `/help` `/quit`）：刚好覆盖验收要的三件事。翻某一帧 / time-travel 到指定帧属交互式调试台范围，留给 P1/P2 的 server + 前端。
- **CLI 不自己造异常族**：能出的错都是框架层已有的（`ModelConfigError` / `CheckpointConfigError` / `LoopConfigError` / `GuardConfigError`），逐个再包一层是投机。启动期这四类翻译成一行人话 + 退出码 1；运行期 `ModelError` / `CheckpointError` 报一句后**交互继续**（重试耗尽不该带走整个会话）。
- **退出码三档**：`0` 正常 / `1` 配置错或没答完 / `130` 被 Ctrl-C 打断（沿用 `128 + SIGINT` 惯例）。不按 `LoopOutcome` 细分成更多档 —— 那会把框架的枚举值语义固化进 shell 契约。
- **`--resume` 失败会中止后续 `-q`**：存档都没找到，多半是环境不对（后端换了 / 会话编号写错），再发一次只是白烧 token。
- **`main(model=..., reader=...)` 两个注入缝**：模型走 `ChatModel` 薄协议（与全仓其他测试同一套替身做法，被测代码一行不改），输入走 `reader` 可调用对象。存储不注入 —— 测试用 `monkeypatch` 换 `build_saver_for`，不给生产代码多开一个洞。
- **模型工具的 `arguments` 展示时解析一次**：事件载荷里是**原始 JSON 字符串**（#10 刻意不预解析）。展示层解析只是为了让 `{"order_no": "..."}` 别带转义引号；解析失败就原样打并标注「畸形 JSON」—— 那本身就是要给用户看的信息（模型填错了参数，下一轮自纠错）。

### 8. 验收证据

- **默认全量**：`710 passed / 65 deselected`（基线 621 → 新增 **89 例**：commands 21 · render 20 · session 12 · app 23 · root_facade 11 · env_template 2，其中 app 那一例起子进程验真实编码行为）
- **真库**：`pytest -m "pg or pg_db"` → `51 passed`；`pytest -m redis` → 3 例因本机 Redis 未启动跳过（环境性，非回归）
- **真实端点**：CLI 六次实测（单工具 / 并行双工具 + reasoning / 中断续跑 / 跨进程 `--history` / 跨进程 `--resume` / `--max-turns` 刹车），全部符合预期
- **Ruff**：`ruff check CharAgent/` + `ruff format --check CharAgent/` 零告警
- **端到端打断用例进了测试**：`tests/test_client_app.py::test_interrupt_then_resume_does_not_rerun_completed_tools` 驱动**真的 `main()`**，用「0.3 秒后 SIGINT」落在第二轮模型调用上，断言事件流里 `[tool_call]` 恰好一次 —— P0 验收第 4 条从此有自动防线，不只靠手工演示
- **`code-review` 双轴修复**：`_dispatch` 原按「先跑 resume、再逐条跑 -q」两段循环写，`break` 判在每次提问**之后** → resume 已经失败时后面的提问照样会发出去；改成先把「要跑几步」排成队列再单循环（失败即止）。`EventPrinter._tag` 原直接 `_TAGS[event_type]` → 未知事件类型会 KeyError（与 docstring 承诺的「原样吐出」不符），改 `.get` 兜底并补用例。

### 9. 遗留（不属本 issue）

**全部逐条列进 [../P0-to-P1-P2.md](../P0-to-P1-P2.md)**（ticket 第 4 条要求），要点：

- **P1**：token 级流式（唯一要动 P0 协议的地方 —— 给 `ChatModel.generate` 加 delta 回调）· HITL 触发与审批（P0 只有「能存能读能恢复」的机制）· **工具超时**（P1-3 必补：现在工具不过外部服务所以没写）· 限流 / 熔断 / 幂等存储 / 分布式锁 / 安全护栏 / 降级 / RAG / 结构化输出 / token 计量 / 日志指标 / 客服 demo / 前端
- **P2**：`events` 表 · 清理策略与合规删除 · 连接池调优 · 8 个插件 · 语义缓存 · 非确定性统计 · trace 回放
- **本机事项（已复核，无需处理）**：issue 07 §9.B 记的「遗留空表 `checkpoints` 待手动清」——2026-09-15 查 `public` schema 已不存在（应是 issue 08 §6.B 重建开发库时顺带清掉的）。当前库里只有 `charagent_` 前缀的六张表（五业务 + 版本表），符合预期

### 10. 交付后：用户要求的环境变量改名 + 双轴 code-review 修复（2026-09-15）

#### A. 环境变量统一加 `CHARAGENT_` 前缀（用户要求）

本项目各子项目**共用同一个根 `.env`**，不带前缀的通名看不出归属、也容易撞车
（先例：`CHARPLOT_*` / `RK_*` / `MENU_*`）。据此把本仓自己读的 9 个变量全部改名：

| 旧名 | 新名 |
|------|------|
| `CHECKPOINT_BACKEND` / `_KEY_PREFIX` / `_TTL_SECONDS` / `_REDIS_MODE` / `_REDIS_MAX_FRAMES` / `_REDIS_URL` / `_POSTGRES_DSN` | `CHARAGENT_CHECKPOINT_*`（同名加前缀） |
| `MODELS_DSN` / `MODELS_ECHO` | `CHARAGENT_DB_DSN` / `CHARAGENT_DB_ECHO` |

词干 `MODELS_` 一并改成 `DB_`（用户选定）：与 issue 08 §6.E 把 `models/` 包改名
`db/` 同源 —— `CHARAGENT_MODELS_DSN` 会让人以为它属于 `model/`（LLM 模型层），
跟数据库无关。

- **共用的** `PGSQL_*` / `REDIS_URL` **故意不带前缀**：它们本来就属于整个项目
  （其他子项目也在用），改了会连带改坏别人的配置。
- **代码侧只改常量的值**（`checkpoint/config.py` 的 7 个 `ENV_*`、`db/config.py`
  的 `ENV_DSN` / `ENV_ECHO`）；常量名与所有调用点一个没动，逻辑零改动。
- **没加旧名回退**：真 `.env` 里这 9 项一项都没配过（已核实），回退分支是给不存在
  的场景写代码。
- 受影响：7 个生产文件 + `.env.example` + `CONTEXT.md` / `adr/0002` /
  `design/02-data-model.md` / `db/README.md` + 根 `CLAUDE.md` / `README.md`。
  issue 07 / 08 的历史记录**回改原样**（那是当时的证据，只在文末追加「交付后
  更名」说明）。
- 防线：`tests/test_env_template.py` 拿配置模块的常量名比对模板，漏写或改歪即红。

#### B. 双轴 code-review（Standards / Spec）发现的缺陷，已修

| # | 缺陷 | 修法 |
|---|------|------|
| 1 | **`--resume` 中途 Ctrl-C 退出码变 0**：`_dispatch` 直接 `return _Repl(...).run()`，而交互循环恒返回 0 —— 已经排过的步骤失败或被打断都不再体现，与 `main()` docstring 的三档契约矛盾 | 改为 `return exit_code or repl_code`，并把优先级写进 docstring（一次性步骤的结论优先于交互的 0） |
| 2 | **`ChatSession.ask` 失败后不回滚那条提问**：`_run` docstring 说的「历史停在上一个完整节点」对 `ask` 不成立；中断后再提问会带上一条**没有答复**的旧问题 | `ask` 在异常路径按切片撤回本次追加；`_run` docstring 改为描述真实契约。原用例把错误行为钉成了期望，一并更正 |
| 3 | **帮助文本与实现冲突**：`/quit 退出 (exit / q 也行)` —— 裸 `exit` 不是命令，照帮助敲会被当问题发给模型 | 改为 `/exit 与 /q 也行 —— 命令都要带前导斜杠` |
| 4 | **新文案踩自己刚写的词表 `_Avoid_`**：`[中断] 本次运行已取消 (kill switch)` 混用 Cancellation 的 `_Avoid_`（中断）与 KillSwitch 的 `_Avoid_`（取消） | 统一为 `[打断] kill switch 已触发, 本次运行停止` |
| 5 | **`--history -q "..."` 静默丢弃问题**（模型零调用、问题不打印、退出码 0） | 与 `-q` / `--resume` 同给时直接报错退出 1，并说明两者不能同时给 |
| 6 | **死分支**：`_run_once` 的 `label = "接着跑" if resume else "提问"`，`"接着跑"` 永不打印 | 删掉 `label`，字面量写回分支里 |
| 7 | **两条路各写一份输出**：`_run_once` 与 `_Repl._report_result` 复制同一段「正文 + 账目」，且前者漏了存档帧数；两处「没有可恢复的快照」措辞也不一致 | 抽成模块级 `_report_result` / `_report_interrupt` / `_frame_note` / `_nothing_to_resume` 四个共用函数（顺带消掉 `_frame_note` 里重复的 `prefix`） |
| 8 | **死常量**：`render.py` 的 `_BOLD` / `_YELLOW` 定义了零使用；docstring 引用不存在的 `paint_event` | 删常量；引用改为 `format_event` |
| 9 | **没人传的接口**：`EventPrinter(reasoning_limit=...)` 零调用方传值 | 去掉该构造参数，直接用模块常量 `REASONING_LIMIT` |
| 10 | **配置内部常量上浮到框架根**：`db.__all__` 导出 `ENV_DSN` / `ENV_ECHO`，于是根门面出现 `CharAgent.ENV_DSN` 这种看不出归属的通名；同级的 `checkpoint` 7 个 `ENV_*` 却不在门面里 | 从 `db.__all__` 与根门面移除（与 `checkpoint` 口径一致），在 `db/__init__.py` docstring 写明理由；测试改从两个 `config` 模块直取 |
| 11 | **Mysterious Name**：`render.py` 的 `_short()` 实为「毫秒 → 短文本」 | 改名 `_format_ms` |
| 12 | 记载失真：「生产代码侧只剩两处小改」（实际另有跨 7 文件的环境变量改名）；本文档「详见 §10」但当时没有 §10 | 本节的 §A 与上面 Comments 已更正 |

**未采纳（附理由）**：审查建议把 `ChatSession.history` 也当投机接口删掉 —— 不采纳：
它是会话的自然读口（一个会话连「聊过什么」都读不到是别扭的），测试在用，仓内亦有
同型先例（`AgentLoop.tool_names` 起初也只有测试在用，issue 10 才接上生产调用点）。

#### C. 本轮补的测试（+12 例，`test_client_app.py` 23 → 35）

审查点名的覆盖缺口逐条补上，其中两条**经变异验证确有牙**（把修复改回旧写法即红）：

| 用例 | 盯什么 |
|------|--------|
| `test_build_model_wraps_the_adapter_with_retry_by_default` | ticket 验收第 2 条的正文（此前只有手工证据） |
| `test_build_saver_for_falls_back_to_the_environment` | 不给 `--backend` 时听环境变量；给了就压过它 |
| `test_max_tokens_is_carried_into_the_options` / `test_max_turns_...` | 两个选项的 parse 映射 |
| `test_truncation_continuation_is_stitched_into_the_answer` | `--max-tokens` 造截断 → 续写两段被拼合 |
| `test_reasoning_events_show_up_in_the_run_output` | reasoning 端到端（ticket 要求四类事件 + reasoning） |
| `test_tool_failure_then_self_correction_ends_with_an_answer` | 失败 → 可操作错误回填 → 改对 → 答复 |
| `test_thread_id_keeps_sessions_apart` | `--thread-id` 会话隔离（甲的帧不出现在乙的历史里） |
| `test_ctrl_c_at_the_prompt_is_a_normal_exit` | 提示符处 Ctrl-C 走正常退路 |
| `test_history_rejects_questions_instead_of_dropping_them` | 上表第 5 条的行为 |
| `test_interrupted_question_exits_with_130` | 一次性提问被打断的退出码 |
| `test_batch_failure_is_not_erased_by_the_following_chat` | 上表第 1 条的行为（变异验证） |

另在测试文件里加了 `_InterruptOnCall`（模型包装：第 n 次调用时把「0.05 秒后
Ctrl-C」挂到事件循环上）—— 打断位置因此是确定的，不靠 sleep 赌时机。

#### D. 复核

- 默认全量：**722 passed / 65 deselected**（本轮修复前 710 → 新增 12）
- `pytest -m "pg or pg_db"` → 51 passed；`pytest -m redis` → 3 skipped（本机 Redis 未起）
- `ruff check` + `ruff format --check` + `pre-commit run` 全绿
- 真实端点回归：改名后 `CHARAGENT_CHECKPOINT_BACKEND=postgres python -m CharAgent.client
  -q "现在 UTC 几点?"` 跑通（工具调用 + 答复，退出码 0）

---

### 11. 交付后：打断后能用大白话续跑（用户复核，2026-09-15）

#### A. 用户提的两个问题与当时的答案

| 问题 | 当时的答案 |
|------|-----------|
| agent 运行中用户取消，能不能正常中断？ | **能**。`_KillSwitch` 取消任务 → 等它收尾（该落盘的快照落完）→ 抛 `KeyboardInterrupt` 给上层报成 `[打断]`。已有 3 条用例（`test_kill_switch_...` / `test_interrupted_question_exits_with_130` / `test_interrupt_then_resume_...`）+ 真实端点实测 |
| 中断后输入「继续」「刚才不小心中断任务了，继续刚才的任务」能不能接着跑？ | **不能**。这是本轮补的缺口 |

#### B. 缺口是什么（真跑一遍才看清）

分派只认 `/` 开头的四条命令，其余一律走 `_ask`。而 `ask` 在打断时已把那条提问
**撤回**（§10.B 第 2 条），于是：

```
用户: 我的订单 20260701123456 到哪了     ← 工具已跑完, 快照落了一帧
       [Ctrl-C]  →  [打断] ...
用户: 继续                                ← 走 _ask, 当成新问题发出去
模型看到的消息: ["继续"]                  ← 原问题没了, 半截任务也没了
```

模型于是另起炉灶，**用户以为在续跑，其实进度丢了**。屏幕上看不出异常 —— 这才是
最坏的地方。

#### C. 修法（含一次方案推翻）

**第一版（已废弃）**：加关键词启发式 `commands.looks_like_continuation`（续跑词 +
短句/回指词 + 否定词），命中且会话被打断过就走 `_resume()`。能用，但多一层误判面
（漏认 → 用户还得敲 `/resume`；错认 → 用户那句话被静默吞掉），21 条用例都在护这个
启发式。

**用户随后提出更根本的质疑**：「直接把『继续』当提示词交给模型不行吗，像 Claude Code
那样？」—— 对。真正的问题不是「分不清句子」，而是**模型手里没有上一轮做完的事**。
于是推翻重做：

| 位置 | 最终做法 |
|------|---------|
| `client/session.py` | 新增 `_reclaim_progress()`：失败路径上去快照取最新一帧，**只做加法**（快照历史比当前长才替换）。`ask()` 里的「撤回提问」随之删掉 —— 撤销一种、收回一种 |
| `client/app.py` | 删掉 `_continuation_request` 与那条分派分支；删掉 `session.interrupted` 与它的提醒（没有消费者了）。文案改为「已完成的工作已收回对话历史: 说一句「继续」就能接着跑」 |
| `client/utils/commands.py` | 删掉 `looks_like_continuation` 及全部常量 —— 这里**不再做意图识别** |
| 净效果 | 生产代码 **-40 行**，用例 **-21 条**（判据）+6 条（收回进度） |

现在的因果关系只有一条：**上下文备齐 → 模型自己判断**。CLI 里没有任何「这句是不是
续跑」的分支。

#### D. 验证：模型自己把话说清楚了

真实端点实测（DeepSeek），打断落在第 2 轮模型调用，然后说一句「继续」：

```
[tool_call]   query_order_status({"order_no": "20260701123456"})
[tool_result] query_order_status ok: 订单 20260701123456: 已发货, 预计 2026-07-05 送达
[打断] kill switch 已触发, 本次运行停止; 快照: InMemoryCheckpointSaver 里 1 帧
[打断] 已完成的工作已收回对话历史: 说一句「继续」就能接着跑 (工具结果就在历史里, 不会重跑)
[reasoning] 用户说"继续"。上一轮我查询了订单状态。…我可以再查一次? 重复调用没有意义。
[tool_call]   get_current_time({"fmt": "iso", "timezone": "shanghai"})
[final]       答完了 · 2 轮 · 3989 tokens
```

**`query_order_status` 全程只出现一次**，而模型明确推理了「重复调用没有意义」，转而去
查当前时间，给出了更完整的答复（还发现「预计 7/5 送达，但今天是 9/16」这个异常）。
这正是设计想要的效果：模型看到已完成的工作，自己决定不重做。

#### E. 迭代过程中抓到的两个问题

1. **一条假的绿**（第一版留下的教训）：端到端用例的强断言原本是
   `out.count("[tool_call]") == 1`，但「把这句当新问题发出去」那条路**恰好也没重跑
   工具**（脚本第三轮是纯文本），断言照过。变异验证（把 `_resume()` 改回 `_ask()`）
   才暴露出来。改成断言「那一轮模型收到的消息逐条对得上」后，变异下直接报
   `assert ['user','user'] == ['user','assistant','tool','user']`。
   **断言要挑能区分两条路的那个量，不是顺手能写的那个。**
2. 我一度认为「让模型自己判断」做不到，理由是要「loop 逐轮回写历史」—— 错了。
   只需把最新快照的历史收回会话历史（一行），上下文就齐了。**评估方案时先找最小
   实现，别被「听起来要重构」吓住。**

#### F. 复核

- 默认全量：**728 passed / 65 deselected**（净 -21 判据 +6 收回 +2 改写）
- `-m "pg or pg_db"` → 51 passed；`ruff check` + `format --check` + `pre-commit` 全绿
- 真机回归两条：`/resume` 路径（`test_interrupt_then_resume_does_not_rerun_completed_tools`）
  与新增的口语路径都实测跑通
- 边界（软保证 / 直线假设 / 与「不重复粒度是 Turn」那条的关系）记在
  `CharAgent/docs/design/06-boundaries.md` §6.7

#### G. 临时加的身份说明（用户要求，2026-09-16）

**起因**：用户问 agent「你的底层模型是什么」，答的是 Claude。排查结论——**配置没问题**：
`base_url = https://api.deepseek.com`，模型名 `deepseek-flash`，**上游响应里服务端自己
回填的 `model` 字段也是 `deepseek-flash`**（进程内能拿到的最硬证据）。是模型在
**瞎认自己**：LLM 没有对自身权重/版本的内省能力，被问到时它做的是「从语料续写一句最像
的」，而语料里 ChatGPT/Claude 的对话被大量转载，于是脱口而出。同一问题两次提问
（一次答 Claude、一次答 DeepSeek）即证明它在生成而非读取。

**修法**：`ChatSession._history` 起始就带这条 system 消息，正文来自提示词文件
（`CharAgent/prompt/templates/system.prompt`，经 `load_prompt("system", ...)` 渲染）。这行**不是**权宜之计——Claude Code 这类产品都这么做（身份写在
system prompt 里，模型照着念）；不写的话会话历史第一条就是用户提问，模型手里毫无依据。

**实测**：连问三次「你的底层模型是什么」，三次都答「CharAgent 演示助手 + DeepSeek 提供的
模型」，且能说清「框架 + 模型」的分工。

**已知局限**（**2026-09-18 已解决**）：原先是提示词里写死 DeepSeek，用 `--model` 换
后端时这句话会失真。现在模型名由 `CliOptions` 传进 `ChatSession`（`resolve_model_name`
按 `--model` → `.env` → 默认值 解析），提示词本身也搬到了
`CharAgent/prompt/templates/system.prompt` 按名加载 —— 说的与实际跑的一致。

**测试**：`test_history_starts_with_the_identity_prompt` + `test_the_model_receives_the_identity_prompt`
（后者钉「真的发给了模型，不只是躺在会话历史里」）；另有一批断言按新形状调整（会话历史
首条变 system，比下标的地方一律改成按角色找）。

#### H. 包改名 `cli/` → `client/`（用户要求，2026-09-16）

**改的是什么**：包目录 `CharAgent/cli/` → `CharAgent/client/`，入口随之变成
`python -m CharAgent.client`。改名动机未记录（推测为给 P1 的 server/client 分工留位）。

**改动面**：包目录 + 4 个测试文件（`test_cli_*.py` → `test_client_*.py`）+ 所有
`from CharAgent.cli ...` 导入 + 帮助文本 / `prog=` / 文档里的 `python -m CharAgent.cli` +
`CLAUDE.md` / `README.md` / `CharAgent/docs/*` / `.scratch/CharAgent/*` 里的路径引用。
**逻辑零改动** —— 全是名字。

**刻意保留的 `cli`**（不是残留，改错会出问题）：

| 位置 | 为什么留 |
|------|---------|
| `client/utils/types.py` 的 `DEFAULT_THREAD_ID` | 它是**存档主键**，与包名没有绑定关系 —— 当时为了不改主键而留 `"cli-main"`。2026-09-18 复核后改成 `"client-main"`：改主键的唯一代价是**旧主键下的档默认取不到**（`load_latest` 按 thread_id 分区取，加 `--thread-id cli-main` 仍可访问），**不存在「数据丢了」或「要连着迁移」**，默认的 memory 后端本就不留档。原措辞把代价说重了 |
| 文档里的大写 **CLI**（「CLI 入口」「CLI 演示」）与 `CliOptions` | 那是**产品概念**（命令行界面），不是包名 —— 包叫 `client` 不代表这个工具不是 CLI。`CliOptions` = 「命令行选项」，同理 |
| ticket 文件名 `10-P0-acceptance-cli-demo.md` | 是**永久链接**（issue 06 / DESIGN.md / PRD 都指向它），改名会断链 |

**验证**：`python -m CharAgent.cli --help` → `No module named CharAgent.cli`（旧名确已消失）；
`python -m CharAgent.client --help` 正常；全量 **730 passed**（与改名前一致）；`-m "pg or pg_db"`
51 passed；`ruff check` + `format --check` 全绿。全仓 `cli` 残留扫描只剩上表三类。

**历史记录的处理**：本文档与 issue 06 / 08、`PRD.md`、`P0-to-P1-P2.md` 里的旧路径**已按新名
订正**（读者照着找得到文件），改名这件事本身记在本节，不散落在各处的历史叙述里。

#### I. 补上「重试在 CLI 里真的发生过」的端到端证据（用户复核，2026-09-16）

**用户的疑问**：终端跑 `python -m CharAgent.client` 到底有没有带重试？

**答复：带了** —— `__main__.py` 调 `main()` 不传参 → `model=None` → `model if model is not None
else build_model(...)` → `build_model` 默认返回 `RetryingChatModel`（`--no-retry` 才关）。
手工探针实测：让模型前 2 次抛 `ModelConnectionError`，跑完 **3 次调用 / 2 行 `[retry]`（退避
0.4s → 0.7s）/ 退出码 0**；`--no-retry` 时 **1 次调用 / 退出码 1**。

**但验收确实缺了一环**（用户追问逼出来的）：CLI 的端到端用例**全都注入 `model=`**，而
`main()` 在注入时**根本不走 `build_model`** —— 于是重试包装在那些用例里一次都没被执行。
原有的两半覆盖救不了它：

| 已有 | 覆盖到哪 |
|------|---------|
| `test_build_model_wraps_the_adapter_with_retry_by_default` | 只断言**对象形状**（直接调 `build_model`） |
| `test_retry_chat_model.py` 12 例 | `RetryingChatModel` 自己 + loop 组合，不经过 CLI 装配 |

**补了 2 例**（`test_client_app.py`）：只换最外层的 `chat_model_from_env`（比注入 `model=`
更贴近真实路径，`build_model` / `RetryPolicy` / `on_retry` 全真跑）—— 正面「前 2 次失败仍答完
+ `[retry]` 两行」，反面「`--no-retry` 一次就失败」。

**变异验证（关键）**：把 `main()` 改成 `... else chat_model_from_env(...)`（绕过 `build_model`）
—— `test_build_model_wraps_...` **照过**（它直接调 `build_model`，不知道 `main()` 已经不用它了），
只有新用例红（`assert 1 == 0`）。**这就是原有覆盖漏掉的那一格：形状对 ≠ 装配用上了。**

全量 **732 passed**（+2）—— 2026-09-18 复核为 **741 passed**。
