# C07 · #14 熔断 + failover

**Status:** done

**Type:** feature

**上游:** `CharAgent/docs/DESIGN.md` §4 ② 的 #14（「三态（关闭 / 打开 / 半开）+ 切换备份模型」）；`.scratch/Charlotte/PLAN.md` §4 组 B

**决定落点:** `CharApp/docs/adr/0025-the-breaker-sits-inside-retry-and-the-backup-is-another-vendor.md`

## 现状

`retry/chat_model.py` 的 `RetryingChatModel` 用**组合包装**的方式给 `ChatModel` 加上了重试（#13 定下的「在哪切一刀」）。熔断是**同一层的另一个包装** —— 不需要动 `agent/loop.py` 一行。

**但两者的嵌套顺序是个真决策，先定下来再动手。**

---

## 一、嵌套顺序（本片的核心决策）

两个候选：

| 方案 | 行为 |
|---|---|
| **A** `Retrying(CircuitBreaker(Model))` | 熔断器看到**每一次物理调用** → 计数准确；熔断打开时抛**不可重试**错误 → 重试**立刻放弃**，不在退避里空转 |
| B `CircuitBreaker(Retrying(Model))` | 熔断器只看到**逻辑调用**的最终结果 → 内部重试 3 次失败才算 1 次熔断失败 → 熔断器**反应迟钝** |

**选 A**（已落地为 `RetryingChatModel(FailoverChatModel(主, 备))`），并靠 `retry/policy.py` 已有的 `is_retryable` 判据落地：熔断器打开时抛的 `CircuitOpenError` **不声明 retryable**，重试层「未声明一律不重试」（#13 已定的规矩）会自动放弃。

**动手时发现的一条硬约束（原计划没写，落在了 ADR-0025 里）**：光有 A 还不够 —— **跳到闸的那一跳必须当场改走备份**。默认阈值 3 正好等于默认重试次数 3，于是「第三次失败把闸拨开」与「重试层认输」是**同一刻**，只按「下次调用前重新挑」的话备份永远等不到出场机会。现在的语义是两条：① 每次发请求前按主 → 备挑一个还放行的；② 刚跳闸的那一跳在同一跳之内立刻改走备份（不等退避）。

## 二、三态

| 态 | 判据 | 行为 |
|---|---|---|
| **关闭** | 默认 | 正常放行，滚动统计失败 |
| **打开** | 连续 N 次失败 | **直接拒绝，不发出请求**；带冷却期 |
| **半开** | 冷却期到 | **只放行一个探测请求**；成功 → 关闭；失败 → 回到打开 |

**三个必须钉住的点**（都有用例）：

1. **打开期间必须真的不发请求** —— 假模型的 `calls` 计数在闸开之后不再增长
2. **半开只放行一个** —— 并发三个调用，只有一个探测过去（其余走备份）
3. **冷却期与阈值可配**，且**注入时间源**（对齐 #13 与 #61 的 `sleep` / `time_source` 注入缝，测试零真实等待）

判据取 #13 那份 `is_retryable`（429 / 5xx / 连接失败 / 超时算账；400 参数错**不算** —— 换个模型发同样的请求一样会错），一次成功把连续计数清零。

## 三、failover

主模型熔断打开时切到备份模型：

- **切换要在轮内可见**：本轮的后续调用走备份，不等下一轮（见上面那条硬约束）
- **开/关要有痕迹**：闸跳 / 闸合 / 切换到谁 —— 落日志（C05 的统一出口）+ 终端一行 `[failover]`（`on_switch` 回调）
- **不产生假账**（对齐 #13 的「双计费不给假账」）：切到备份模型后，**用量与金额按实际调用的那个模型记**
- 备份模型自己也有熔断（两侧各一个闸）

## 四、验收

- [x] 用假 `ChatModel` 钉住：连续 N 次失败 → 进打开态 → **再调用时假模型计数不增加**（真的没发请求）
      —— `test_after_the_breaker_opens_the_dead_model_is_not_called_at_all`（主模型停在 3 次不动，后续全走备份）
- [x] 打开期间抛出的异常**不可重试** → `RetryingChatModel` 立刻放弃、不空转
      —— `test_the_retry_layer_gives_up_at_once_when_the_breaker_opens`（5 次尝试只用了 3 次，退避只等了前面那两次）
- [x] 冷却后进半开，**只有一个探测请求被放行**（用一个可控的假模型 + 并发发起多个调用验）
      —— `test_after_the_cooldown_only_one_probe_is_let_through`（三个并发任务，探测位被卡住时另外两个走备份；用一把 `asyncio.Event` 造「探测在飞」，不靠时序运气）
- [x] 探测成功 → 关闭；探测失败 → 回打开
      —— `test_a_successful_probe_hands_the_traffic_back_to_the_primary` / `test_a_failed_probe_reopens_the_breaker_and_switches_again`
- [x] failover：主模型打开后，后续调用走备份模型；`runs` 里记的是**实际模型**与对应的单价
      —— `client/session.py` 每段运行开一本服务台账，收尾写 `runs.model`（`test_a_failed_over_run_tells_the_recorder_the_model_that_answered`）；价目表按模型名查，于是金额自动按备份的单价算。一趟里两家都用过记组合名（C23 起段内那趟**按各家分别算再相加**，见那张票；只有 HITL 跨段那一路仍金额留空并作废旧金额）
- [x] 时间源可注入 → 测试零真实等待
      —— `time_source` 注入缝（`FakeClock`），与 #13 同一套惯例
- [x] `agent/loop.py` **零改动**（与 L1a 的同款纪律：加一层能力不需要动循环核心）
      —— `git status` 里没有这一项
- [x] 现有用例全绿
      —— 框架 1470 passed / 132 deselected（基线 1414，净 +56）；CharApp 355 passed

## 开工前要定的（已定，2026-10-04）

| # | 问题 | 结论 |
|---|------|------|
| 1 | 备份模型选谁 | **另一家（CLOSEAI，OpenAI 兼容端点）** —— 与 `PLAN.md` §6.1 的 D2 一致：同 provider 另一档会一起挂，等于没切。用户在 `.env` 里配好 `CLOSEAI_CHAT_MODEL`（`openai:gpt-6-luna`）与它的价目表（cache_miss 1 / cache_hit 0.1 / output 5，三档不分峰谷） |
| 2 | 阈值取多少 | **连续 3 次瞬态失败 / 冷却 30 秒 / 半开 1 个探测**（构造参数可覆盖） |
| 3 | 价目表要不要支持多 provider | **不用改** —— 表本来就是「模型名 → 价」，多一条 `gpt-6-luna` 即可；真正的活是**记账那一列要记实际服务的那家**（见「谁服务谁记」），否则按主模型的单价给备份答的话算钱就是假账 |

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/retry/circuit.py` | **新**：`CircuitBreaker` 三态闸（计数 / 冷却现算 / 半开单探测 / 只数瞬态失败 / 取消归还探测位不记账 / **迟到的结论不参与**）+ `CircuitPolicy` 规矩本（阈值 / 冷却 / 时间源 / 失败判据） |
| `CharAgent/retry/failover.py` | **新**：`FailoverChatModel` 主备包装（挑落点 / 跳闸当场改走备份 / 两侧各一个闸 / `on_switch` 通知 / 关两侧连接池） |
| `CharAgent/retry/serving.py` | **新**：`ServingRecord` 运行级服务台账 + `serving_scope`（contextvars，按运行隔离） |
| `CharAgent/retry/utils/types.py` | `CircuitState` / `ModelSwitch` / `SwitchCallback` |
| `CharAgent/retry/utils/errors.py` | `CircuitOpenError`（继承 `ModelError`：既有的降级路径原样接住；带 `retryable=False`：重试立刻放弃） |
| `CharAgent/retry/__init__.py` · `CharAgent/__init__.py` · `CharAgent/client/__init__.py` | 门面导出（根门面的防漂移用例守着） |
| `CharAgent/client/session.py` | 每段运行（`ask` / `resume`）开一本服务台账；`_record` / `_record_unfinished` 按它记模型名（`_recorded_model`） |
| `CharAgent/client/app.py` | `build_model` 装配主备 + 重试两层；`fallback_model_from_env`（三个 `CLOSEAI_*` 变量，缺一即无备份）；`_switch_notice` 终端提示 |
| `CharAgent/model/utils/config.py` | `MODEL_NAME_SEPARATOR` + `join_model_names`：一趟运行用过不止一个模型时名字怎么合（唯一一处定义，retry 与 db 两边都取它） |
| `CharAgent/db/recorder.py` | 收尾时读运行行（`_run_row`）：与该行已记的模型名**再合一次**（HITL 两段落在不同模型上），并把前一段那笔金额**作废**（`void_cost`）；`_cost_of` 改成收已读好的行（不再自己读） |
| `CharAgent/db/repositories/runs.py` | `settle(..., void_cost=)`：把已有金额清掉并按这次的原因重记（ticket 28 那条「给 None 不清金额」的**唯一例外**，条件写死在 docstring 里） |
| `.env.example` / `.env` | 备份模型三个变量 + 价目表加 `gpt-6-luna` 一条 |
| 测试 | `tests/test_retry_circuit.py`（20）· `tests/test_retry_failover.py`（23）· `tests/test_retry_serving.py`（6）**新增**；`test_db_recorder.py` 加三条（两段两家 -> 组合名 + 金额留空 / 两段同一家 -> 不叠 / 会话已拼过的不再拼）；`test_client_app.py` / `test_client_session.py` / `test_env_template.py` 补接线与守卫；`CharAgent/tests/doubles.py` 加 `no_backup_endpoint` 夹子（两个套件共用一份，conftest 各 import 一次） |
| 文档 | `CharApp/docs/adr/0025`（新）· `CharAgent/docs/DESIGN.md` §4 ② #14 · `docs/difficulties/02-stability.md` #14 · `CharApp/docs/PLAN.md` ADR 索引 |

## 实施记录

- **编排**：熔断是 `retry/` 包里的第四个零件（与 policy / executor / chat_model 并列），failover 与台账各一个文件 —— 三个模块各管一件事（闸只管放不放，包装只管挑谁，台账只管记账），`agent/loop.py` 与业务侧 `CharApp/minimall/tools.py` 一个字没改。
- **嵌套顺序**：`RetryingChatModel(FailoverChatModel(主, 备))` —— 闸看得见每一次物理调用；打开时抛的 `CircuitOpenError` 不声明 `retryable`，`executor.py` 的既有判据自动放弃（没有为它加任何分支）。
- **一跳之内改走备份**：这一条是动手时才发现的（见上），没有它默认配置下 Failover 永远不生效（阈值 3 = 重试 3）。
- **账目**：模型层每次**成功**往台账记一个名字（失败产出 0 个 token，不上账）；会话收尾读它写 `runs.model`。熔断切过去的运行 → 按备份的单价算钱；一趟里两家都用过 → 组合名 `主+备` → 价目表查不到 → 金额留空并写明原因（不给假账：按任何一家的单价算都是错的）。
- **测试里两个「不靠运气」的做法**：并发那条用 `asyncio.Event` 把探测卡住（而不是睡眠）；时间全部走 `FakeClock`，一次真实等待都没有。
- **一道新夹子**：`conftest` 默认把「备份端点」摘掉（删三个环境变量 + 换掉工厂）—— 本机 `.env` 里配了另一家的 key 不该改变用例的装配结果，更不该让本该离线的用例去调真端点。删环境变量不够：CLI 入口自己会读 `.env`，所以工厂也要换（两处都写在夹子的 docstring 里）。
- **`agent/loop.py` 一行未改**（票面纪律），`CharApp/minimall/tools.py` 同样一行未改 —— 熔断与 failover 对业务完全透明。

### 收尾复核（两轴 code review）抓出的三条

写完第一版后按惯例走了两轴复核（标准 + 票面），抓出三条，都已修并补了用例：

1. **冷却期会被在飞的迟到失败重置**（真缺陷）：一次调用发出去之后别的调用把闸拨开了，它在闸开**之后**才失败回来 —— 那一笔照着记会再跳一次闸，把冷却期**从头再数**；并发一多，冷却期被一次次推后（实测：阈值 3 跳闸于 t=0，t=10 的迟到失败把冷却推到 t=40，t=35 本该半开却还开着），同时违反「跳闸后计数停在阈值上」那句承诺。修法：**迟到的结论一律不参与**（判据 = 闸不关着且这次不是那个探测），失败的与成功的都不动闸；用例三条（迟到失败 / 迟到成功 / 探测照记）。
2. **跨段记账可出假账**（真缺陷）：同一趟运行的两段（HITL 挂起 -> 人确认 -> 续跑）可能落在不同模型上，而第二段收尾会把 `runs.model` **覆盖**成自己那家的名字、金额却按累计用量（含前一段）算 —— 正是票面点名要避免的「按备份单价算主模型答的话」。修法：记录员把行上已记的名字与这一段报的**合起来**（`join_model_names`），且这时**把已有的金额作废**（`void_cost`）—— 前一段那笔是另一套单价算的。用例三条（含「会话那边已拼过的不再叠字」）。
3. **参数成堆 / 重复的测试夹具**（判断项）：阈值 / 冷却 / 时间源 / 判据四样总是一起走，收成 `CircuitPolicy`（与 `RetryPolicy` 同形，校验也在构造期）；「默认不碰备份端点」那道夹子在两个 conftest 里各有一份，上浮到 `tests/doubles.py` 由两边 import（与假库上浮到 `db/testing.py` 同一条理由）。`refusal()` 那处「公开但只有自己用」的方法收成私有。

复核没改的三条判断：取消语义（`record_abort`）与 `aclose` 关两侧、`on_switch` 收序列、「缺一即无备份」的 fail-open —— 都不是票面逐字要求的，但每一条都对应一个真实的边界情形，且在 ADR / 代码注释里写明了理由。
