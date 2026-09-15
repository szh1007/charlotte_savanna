# 07-P0-6 — Checkpoint 序列化 + 三实现 + time-travel

**What to build:** CheckpointSaver 协议 + 序列化协议（JSON 主格式 + 自定义 encoder/decoder 处理 datetime/嵌套 dict + schema_version 向前兼容迁移），InMemory / Redis（KV 快照 + TTL）/ Postgres（强一致 + 历史）三实现配置切换（ADR-0002）。每 Turn 结束落盘（含 HITL 挂起点），支持断点续跑（不重复已完成动作）与 time-travel 回溯（恢复历史 checkpoint → parent_id 新分支）。

**Blocked by:** 04

**Status:** done

- [x] 序列化协议：JSON + 自定义序列化器 + schema_version；版本升级走迁移函数，旧快照可读回（#5）
- [x] InMemory / Redis / Postgres 三实现经同一协议可配置切换（ADR-0002）
- [x] 断点续跑：快照后中断 → 从 checkpoint 恢复 → 不重复执行已完成动作（#5）
- [x] time-travel：恢复历史时刻 → parent_id 产生新分支
- [x] 双实现语义对比测试：同一事件序列喂 Redis / Postgres，恢复结果一致；Postgres 历史可回溯（ADR-0002 验收）
- [x] 挂起点快照：HITL 挂起也落快照（为 #25 恢复而非重跑打底）

## Comments

> 阅读提示：§1~§9 是**首版交付**的记录（当时的 Redis 只留最新一帧、SCHEMA_VERSION=2）；**当前状态以 §10（交付后重构）为准** —— Redis 改为 Stream 流式全历史（可切回 latest）、观察值从 state 分层到 metadata（SCHEMA_VERSION=3）、新增可读历史视图。

**2026-09-13 实施完成**。落点 `CharAgent/checkpoint/`（顶层包，对齐 DESIGN.md 目录结构与 issue 04/05/06 的包组织惯例；静态零件按惯例收进 `utils/`），门面 `__init__.py` 导出公共 API；agent loop 只加一处接线（`saver` / `thread_id` / `resume()`），既有 341 个用例原样通过。

### 1. 模块落点与分工

| 文件 | 职责 |
|------|------|
| `checkpoint/base.py` | `CheckpointSaver` 协议（async）：save / load_latest / load / list_history / aclose + `capabilities` |
| `checkpoint/memory.py` | `InMemoryCheckpointSaver`：能力最全的标尺（有历史、不过期、进出深拷贝） |
| `checkpoint/redis.py` | `RedisCheckpointSaver`：`{前缀}:ckpt:{会话}` 整条 JSON + TTL，**只留最新一帧** |
| `checkpoint/postgres.py` | `PostgresCheckpointSaver`：一帧一行 + `state` JSONB，全历史、可回溯 |
| `checkpoint/serialization.py` | `CheckpointCodec`：JSON 编解码 + 标签机制 + 自定义类型注册 + 版本迁移应用 |
| `checkpoint/config.py` | `checkpoint_saver_from_env` / `build_saver` / `postgres_dsn`：配置切后端（ADR-0002） |
| `checkpoint/utils/` | `types`（Checkpoint / CheckpointState / Suspension / 能力声明 / 标识符校验）· `errors`（五类错误）· `migrations`（版本翻译）· `pending`（找出还欠结果的工具调用）· `fields`（取值校验）· `ddl`（建表语句，与 issue 08 的 alembic 共用） |

### 2. 序列化协议（ticket 第 1 条）

- **JSON 主格式**：`ensure_ascii=False`、`allow_nan=False`（NaN / Infinity 不是合法 JSON，宁可报错也不写出别人读不懂的字）；存储用紧凑一行，给人读与快照对比用 `indent=2`（同一份数据两种版式）
- **标签机制**：装不进 JSON 的类型写成 `{"__charagent_type__": "datetime", "value": "2026-09-13T10:00:00+00:00"}`。**恰好两个键**才算标签 —— 业务数据里碰巧同名的字段不受影响（不必给自己留转义符）。出厂只带 `datetime`，其余走 `codec.register_type(类型, name=..., encode=..., decode=...)`；装不进又没注册的类型报错并**给出注册示例**（可操作错误，与 #2 同一条风格）
- **schema_version**：当前 `2`。v1 = state 为**裸消息列表**（早期原型只够「说过什么话」），v2 = 结构化字典（messages + 计数器 + 正文片段 + 挂起点）。`MIGRATIONS` 逐级升级，缺某一级或版本**比当前新**都报错 —— 尤其是后者：硬读未来格式会把字段读错位，悄悄给出一份错的进度，比报错危险得多
- 读取顺序：**先拆行李牌、再翻译版本**（翻译函数面对 Python 值更直白）；老 v1 快照里 `created_at` 是裸 ISO 文本，v2 是标签 —— 两种都认，这就是「向前兼容」落到代码里的样子
- 快照测试（#63）：`tests/fixtures/checkpoint_v2.json` 逐字锁定当前格式；`checkpoint_v1.json` 是手写的老格式样本，专门验「老快照读得回来」

### 3. 三实现与能力差异（ticket 第 2 条 / ADR-0002）

| 实现 | 历史 | 会过期 | 关键语义 |
|------|------|--------|---------|
| InMemory | 有 | 不会 | 排序键 = (时刻, 编号)，与 Postgres 的 `ORDER BY` 对齐（否则「乱序存入」时两者给出不同历史，双实现对比就假了） |
| Redis | **无** | 可配 TTL | 只有最新一帧；`load(id)` / `list_history` 抛 `CheckpointCapabilityError`（明说做不到，不返回空结果让人误判「没存过」） |
| Postgres | 有 | 不会 | 一帧一行；`ON CONFLICT (checkpoint_id) DO NOTHING`（同编号重复保存幂等）；`parent_id` 自引用外键 `ON DELETE SET NULL`（删一帧不连带删掉挂在它下面的分支） |

- **能力用 `capabilities` 声明**（history / ttl），调用方可以先问一句再决定（想 time-travel 就该先看有没有历史），而不是等报错
- **Postgres 用同步驱动 + `asyncio.to_thread`**（实测踩到的坑）：psycopg 的 `AsyncConnection` 在 Windows 默认的 Proactor 事件循环上直接报错（要求换 SelectorEventLoop）。本项目在 Windows 上开发与演示，不能把「换事件循环」这种前置条件塞给使用方；改法是把阻塞调用（含建连接）挪进工作线程，接口仍是 async。Django 的 async ORM（sync_to_async）走的就是这条路。已写进模块 docstring 与 `02-data-model.md §3`
- 连接**只有一条 + 一把 asyncio 锁排队**（连接不能并发用）；连接池属 P2-10，现在不提前铺开
- Redis 键名：设计文档原写 `ha:ckpt:{thread_id}`，`ha` 无出处且容易让人以为另有其义 → 改为 `charagent:ckpt:{thread_id}`（可用 `CHECKPOINT_KEY_PREFIX` 覆盖），文档同步更正
- 配置切换（ADR-0002）：`CHECKPOINT_BACKEND` 选 memory / redis / postgres；Redis 地址回退共用 `REDIS_URL`，Postgres 连接串优先 `CHECKPOINT_POSTGRES_DSN`、否则用共用的 `PGSQL_*` 经 `make_conninfo` 拼（不手拼 f-string：密码里的 `@` `:` 会把连接串拼坏）；后端名**不做同义词猜测**（写 `postgresql` 得到「未知后端 + 可选值」的报错，猜错就是连错库）

### 4. 断点续跑与 time-travel（ticket 第 3、4 条）

接线形状（`AgentLoop`）：`saver=` + `thread_id=` 成对给（只给一半是配置错，构造期报错；thread_id 还在这里过一次标识符校验 —— 它之后会变成存储键名）；`run(messages, run_id=...)` 契约不变；新增 `await loop.resume(checkpoint, run_id=...)`：

- **不重复已完成动作**：以快照里的历史为起点续跑，工具结果都在历史里，所以已完成的 Turn 不会被重跑（用例用「数调用次数」的工具钉住：第一段跑 1 次工具 → 恢复后仍是 1 次）
- **计数器接续**：turn_count / total_tokens / truncation_count 从快照接着算，于是轮数与 token 预算**跨断点仍然算数**（用例：累计 token 已超预算时，续跑第一时间就刹车，一次模型调用都不发）
- **口径**：`LoopResult.turn_count` 是累计值（含续跑前），`turns` 只含本次 run 的轮次 —— 已在 `agent/utils/types.py` 写清，免得有人按 `turn_count == len(turns)` 去断言
- **time-travel**：从老快照恢复 → 新帧的 `parent_id` 指向那帧老快照，老线原样保留（历史长成一棵树）；`run_id` 可显式传新值表示「另起一次运行」
- **只加不改**：没配 saver 时行为与 issue 04/05 完全一致；普通 `run(messages)` **不**替调用方补做历史里的欠账（补做只在 resume 路径，语义明确）—— 这条边界有专门用例

### 5. 挂起点快照（ticket 第 6 条，为 #25 打底）

- `Suspension`（reason + 还欠结果的工具调用 pending + approval_id）随快照存取；`utils/pending.py` 的 `pending_tool_calls(历史)` 从 wire 形状里找出「已请求但没有结果」的调用 —— 注意不能按 id 建字典（上游每轮的 `tool_call_id` 会重复，如 `call_0` 每轮重来），只能顺着历史走、只记最近一批
- **`resume` 会先补做欠下的调用再继续**：历史末尾是「带 tool_calls 但没有结果的 assistant 消息」时，直接执行那几条、回填结果、记一轮（也落一帧快照），然后才问模型 —— 这就是「从挂起点恢复而非重跑」。用例断言：补做恰好一次、模型第一次被问到时历史末尾已经是工具结果、事件顺序仍是 `tool_call → tool_result → final`
- **触发**（哪些工具需要审批）不在本 issue：属 P1-7 HITL；P0 交付的是「能存能读能恢复」的机制

### 6. 验收证据

- 新增 **127 个用例**（7 个测试文件）：`test_checkpoint_serialization.py`（33：编码 / 解码 / 标签边界 / 自定义类型 / JSON 文本 / 状态与记录往返 / 迁移 / 快照对比）· `test_checkpoint_memory.py`（16：存取 / 历史 / limit / 幂等 / 双向深拷贝 / 排序口径 / 能力）· `test_checkpoint_config.py`（15：后端选择 / URL 回退 / TTL 解析 / DSN 拼装 / 缺变量报错 / 同义词不猜）· `test_checkpoint_redis.py`（19：键格式 / 只留最新 / TTL 与刷新 / 过期 / 能力错 / 异常包装 / 谁建谁关 + 2 个真 Redis 用例）· `test_checkpoint_resume.py`（18：每 Turn 落盘 / 不重跑 / 计数器接续 / time-travel 分支 / 挂起点补做与事件 / 配置与落盘失败边界）· `test_checkpoint_postgres.py`（15：建表幂等 / 历史顺序 / 幂等保存 / 分支 / 外键删除动作 / JSON 列形状 / datetime 与挂起点往返 / 关连接后复用）· `test_checkpoint_backends.py`（11：**双实现语义对比** —— 同一脚本喂三种存储，恢复出的进度与续跑结果一致）
- **默认全量**（全替身，无外部依赖）：`447 passed / 32 deselected`，Ruff check + format 零告警
- **真后端实跑**：`pytest -m pg` → `19 passed`（本机 PostgreSQL 18.4；每个用例用自己的会话号，跑完删掉那个会话的行，可以放心跑在开发库上）；`pytest -m redis` → `2 passed`（本机 Redis 可用后实跑：存进去的确实是那串 JSON，TTL 剩余落在配置范围内）
- **真实端点回归**（改动过 agent 层，按 `tests/integration/test_loop_real.py` 的约定必跑）：`pytest -m integration tests/integration/test_loop_real.py` → `2 passed / 1 failed`，失败项是**既有偶发**（`test_loop_multi_turn_tool_path_httpx` 的前置断言「turn 1 必须产生 reasoning_content」—— 上游这次没吐思维链；同用例连跑 3 次为 过 / 挂 / 过，与本 issue 改动无关：本 issue 不碰 model 层与 reasoning 解析）
- 测试策略：`redis` / `pg` 两个 marker 加入 addopts 默认排除（与既有 `integration` 惯例一致，默认测试仍是「全替身、零外部依赖」）；Redis 默认用 `tests/doubles.py` 的 `FakeRedisClient`（只实现 SET / GET，时钟可注入 —— TTL 到期用「快进时钟」验证，不真等）
- 本地开发库的表已与 `utils/ddl.py` 对齐（该表是本轮测试首次创建、当时为空）：外键动作由 `NO ACTION` 改为 `ON DELETE SET NULL`（用 `ALTER TABLE ... DROP CONSTRAINT / ADD CONSTRAINT`，非破坏性，表与数据未动）；这张旧表随后被 §9.B 的表名修正取代（现用 `charagent_checkpoints`）

### 7. 未采纳（附理由）

- **`delete_thread` / TTL 清理策略**：GDPR 级联删除属 P2-11（#28），清理策略属 P2-2（#12）；现在加一个没人调的删除方法属投机
- **把落盘挂到 `after_turn` hook 上**：hook 的插件异常是「隔离留痕」，而快照存不下是可靠性故障，必须让调用方看见 —— 两者可靠性要求不同（ADR-0007 的分工）。所以走 `saver` 直连 + 异常向上抛
- **在 loop 外面包一层 driver 驱动落盘**：每轮的时机只有 loop 自己知道（`_record_turn` 是唯一准确的位置），外面包一层要么拿不到时机、要么得靠 hook 绕回去（同上一条的毛病）
- **给 `AgentLoop.run` 加 `resume=` 参数（而非独立方法）**：`run(messages)` 是 issue 04 已验收的契约，独立 `resume(checkpoint)` 两个动词各自明确，也省掉「两个入口互斥校验」的别扭
- **为 `postgresql` 之类写法做同义词归一**：猜错就是连错库；报错里列出可选值更安全
- **`CheckpointState` 直接存 `LoopOutcome` / `FinishReason` 枚举**：快照是长期留在存储里的东西，存枚举取值（字符串）才是跨版本稳定契约（枚举改名 / 加成员不该让老快照读不出来）
- **`fakeredis` 依赖**：本包只用到 SET / GET 两条命令，手写替身零依赖且时钟可控；真实语义由标记 `redis` 的用例在真 Redis 上补验

### 8. 遗留（不属本 issue）

- 挂起点的**触发**（哪些工具要审批、`needs_approval` 状态、审批表与超时降级）→ P1-7 HITL（#25）
- `checkpoints` 表纳入 alembic 版本化管理 → issue 08（P0-7；DDL 已放在 `checkpoint/utils/ddl.py`，迁移直接复用同一份 SQL）
- 连接池 / 水平扩展下的连接管理 → P2-10（#66）
- checkpoint 历史的保留与清理策略 → P2-2（#12）；数据合规级联删除 → P2-11（#28）
- 根 `CharAgent/__init__.py` 门面仍未导出 agent / stream / hooks / retry / checkpoint 的公共 API（既有不一致，与 P0-3~P0-6 同批处理）
### 9. 交付后修正（2026-09-13）：集成测试两处偶发 + 表名撞名

**A. 集成测试偶发**（都是既有问题：本 issue 不碰 model 层、也不碰截断处理逻辑，`git diff` 里与截断相关的行只出现在 checkpoint 的读写路径上）：

1. `test_loop_multi_turn_tool_path_httpx` / `_sdk` 的前置断言「turn 1 必须产生 reasoning_content」——上游偶尔不吐思维链，此时用例**测不出想测的东西**，红着还会被误读成「框架坏了」。改为 `pytest.skip("本次上游未产出思维链, 用例失去防线意义")`（用户 2026-09-13 确认的修法）
2. `test_max_tokens_forces_length_truncation` —— 参数算错了：`max_tokens=128` 约合 100 汉字/片，500 字长文需 5~6 片，而 `max_truncations=3` 会**先耗尽预算**（`outcome=TRUNCATION_LIMIT`、`content=None`），于是时红时绿。放宽到 10，只保留「有界」这一点（不加 skip：这一例的前置是满足的，它真的在验 CONTINUE 拼合路径）
3. 证据：连跑 3 次 → `3 passed` / `3 passed` / `2 passed + 1 skipped`（第 3 次上游没吐思维链，走的是新加的 skip 分支）

**B. 表名撞名（本次发现并修）**：本包建的 `checkpoints` 表与 `langgraph-checkpoint-postgres` 的同名表在**本项目共用的 PG 库**里撞名 —— 后者 `setup()` 里的 `CREATE TABLE IF NOT EXISTS checkpoints` 会因「表已存在」被静默跳过，随后 INSERT 报「列不存在」，报错信息与真正的原因（撞名）毫无关系，极难排查。修法：表名改为 `charagent_checkpoints`（`utils/ddl.py` 常量 + `02-data-model.md` §2/§3 + `postgres.py` docstring 同步；测试里两处裸 SQL 改为引用常量）。

**待手动清一次**：旧的空表 `checkpoints`（0 行，早期测试产物）仍在 `public` schema 里，会继续影响 LangGraph 的 PostgresSaver demo —— 建议手动 `DROP TABLE checkpoints;`（本会话没有 DROP 权限，未执行）。

### 10. 交付后重构（2026-09-13）：Redis 流式历史 + 观察值分层 + 回放调试

**缘起**（用户提问 → 决策 → 一次做完，不新开 issue）：首版交付后确认「Redis 只留最新一帧、PG 留全历史」，用户提出「只留最新够用吗？langchain/langgraph 都是每一步都存下来」—— 核实结果：`BaseCheckpointSaver` 协议层就要求 `list()`（本机源码确认），LangGraph 任何后端都留历史；但官方 Redis 实现本身也分两档（`RedisSaver` 全历史 / `ShallowRedisSaver` 只留最新）。用户诉求：三个实现都能**翻历史、回溯分叉点、回放调试**（「仅实现断点续跑对学习理解不够用」）。

**四个决策**（用户选定）：① Redis 用 **Streams** 存历史；② 旧的「只留最新」**保留为模式**（`mode="latest"`，对齐官方 ShallowRedisSaver），默认 `history`；③ 回放调试做到「**每帧补调试元数据** + **可读历史视图**」两档（不导出轨迹文件）；④ 一次做完，改动记在本 issue。

#### 改动清单

| 面 | 改动 |
|----|------|
| 数据形状（v2 → v3） | `CheckpointState` 只留**进度**（messages / 计数器 / content_parts / suspension）；新增 `CheckpointMetadata`（观察值：`source` + `turn_tokens` + `turn_elapsed_ms` + `tool_names` + `content` + `finish_reason` + `outcome`）与 `CheckpointSource` 枚举（loop / fork / suspension） |
| 迁移机制 | 迁移函数的输入从「state 载荷」升格为「**记录主体**」（`{state, metadata}`）—— 因为真实迁移常要在两者之间搬字段；v2→v3 就是一次**字段搬家**（三个观察值从 state 进 metadata），顺带把迁移机制真的跑通第二级 |
| Redis | 重写为两档：`history`（默认）一个会话一条 **Stream**：`XADD` 追加（带 `MAXLEN` 精确裁剪）、`XRANGE`/`XREVRANGE` 翻历史与取最新、`EXPIRE` 续 TTL；`latest` 保持原语义（`SET`/`GET`，键名带 `latest` 与流键区分）。按编号取帧需带 `thread_id`（帧按会话分区）—— 协议相应加了可选提示参数 |
| Postgres | 新增 `metadata` JSONB 列（身份字段成列 + 进度 / 观察值两个 JSON 列）；`ensure_schema` 增加一条幂等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，更早版本建的表自动补列（实测老表升级成功） |
| loop | `_record_turn` 组装观察值（本轮 token / 耗时增量 / 工具名 / 来源），`_save_checkpoint` 收 metadata；`_run` 标来源：续跑第一帧 `fork`、补做挂起调用那帧 `suspension`、其余 `loop` |
| 历史视图 | 新增 `checkpoint/utils/history.py`：`summarize`（一帧一行摘要）、`format_history`（按**终端显示宽度**对齐的表格，汉字算两格）、`display_width`；分叉看「父帧」列即知 |
| 配置 | `CHECKPOINT_REDIS_MODE`（history / latest）、`CHECKPOINT_REDIS_MAX_FRAMES` |

#### 证据

- 默认全量：**482 passed / 33 deselected**；`pytest -m pg` → **19 passed**（真 PostgreSQL，含「老表补列」路径）；`pytest -m redis` → **3 passed**（真 Redis，含流式历史的翻历史 / 按编号取 / 裁剪 / TTL）；Ruff check + format 零告警
- 新增 / 调整用例：`test_checkpoint_history.py`（8，历史视图与对齐）、`test_checkpoint_serialization.py`（39，含 v2→v3 迁移与 v3 格式锁 fixture）、`test_checkpoint_redis.py`（28 + 3 真 Redis，两档模式）、`test_checkpoint_postgres.py`（15，两个 JSON 列）、`test_checkpoint_backends.py`（12，**四实现**参数化：内存 / Redis-history / Redis-latest / Postgres）、`test_checkpoint_resume.py`（20，含来源标记与观察值）、`test_checkpoint_config.py`（18）
- fixture：新增 `checkpoint_v3.json`（当前格式锁）；`checkpoint_v2.json` 转为「老格式样本」（专测字段搬家），`checkpoint_v1.json` 保留（裸列表 → 结构化）

#### 未采纳与遗留

- **不按 super-step 粒度存帧**（LangGraph 那种「每个节点一帧 + 另一张 writes 表」）：我们的粒度是「每 Turn 一帧 + 挂起点写在 state 里」，已够「恢复而非重跑」；更细的粒度留待真有节点级编排需求时再说
- **Redis 不做 checkpoint_id → 条目编号的索引键**：按编号取帧现在是翻账本（先便宜的 JSON 解析比对编号，命中才完整解码）；帧数上量后再加索引键（LangGraph 的做法是 JSON + RediSearch 搜索索引）
- **不导出轨迹文件**（用户本轮未选）：P2 的 trace 回放（#59）再统一做
- 遗留项沿用 §8（挂起触发归 P1-7、表结构归 issue 08 的 alembic、连接池归 P2-10、清理策略归 P2-2 / #28）

### 11. 交付后更名（2026-09-15，issue 10）

`CHECKPOINT_*` 七个环境变量**统一加 `CHARAGENT_` 前缀**（用户要求：本项目各子项目
共用同一个根 `.env`，不带前缀的通名看不出归属，也容易撞车 —— 与 `CHARPLOT_*` /
`RK_*` / `MENU_*` 同一套命名法）。本文档 §1~§10 里的旧名保留原样：那是当时的证据，
不该回头改写；**当前生效的名字**见 `checkpoint/config.py` 的 `ENV_*` 常量、
`.env.example` 与 issue 10 §10。测试与代码里走的都是常量，改名不涉及逻辑。
