# C23 · 一趟里换过家时，金额按各家分别算（#14 的账目补完）

**Status:** done

**Type:** feature

**上游:** `CharApp/docs/adr/0025`（决定 6 与 7）；`CharAgent/docs/DESIGN.md` §4 ② 的 #14；C07 收尾时留下的那条边界

## 现状（开工前读码核实）

C07 落地后，「谁服务谁记」只做到了**名字**那一半：

| 事实 | 证据 |
|---|---|
| 一趟里两家都用过 -> 记组合名（`主+备`），而**金额留空** | `db/recorder.py` 的 `join_model_names` 那一跳 + 价目表按名查 -> `no_price` |
| 逐轮用量其实**是留着的**：`TurnRecord.response`（含 `usage`）与本轮 `tokens` | `agent/utils/types.py` 的 `TurnRecord` |
| 缺的只有「哪一轮是哪家答的」 | 模型层知道（`slot.name`），但没往外带 |
| 金额的算路是单模型假定：`cost_of(model, 五列, moment)` | `db/cost.py` |

于是典型的 failover 形态（主模型**失败的尝试**不产出 token、只有备份答过话）按备份单价算**是准的**；
只有「主模型先成功答过至少一轮、之后才跳闸」（或 HITL 两段分属两家）才落到「金额留空」。

## 一、做法（三处，都不动库表、不动 loop）

1. **服务方的名字跟着响应走**：`FailoverChatModel` 每次成功返回时
   `response.model = slot.name`（价目表的键；覆盖上游回显 —— 回显说的是上游自己是谁，
   还可能带版本号）。这一层本来就知道谁答的话，此前只是没往外带。
2. **记录员按逐轮归属归并用量**：`db/recorder.py` 的 `_usage_by_model(result)` 顺
   `LoopResult.turns` 把每一轮的 `usage` 归到 `response.model` 名下（同一家的几份相加；
   有一份没报某一档就整体报「没报」，与运行行那五列同一条纪律）。
3. **拆账那条算路**：`db/cost.py` 新增 `ModelUsage` 与 `cost_of_split(...)` —— 各查各的
   价、各判各的峰谷、各推各的缺档，最后相加。原先的 `cost_of` 拆成「公开薄壳（含
   「小到存不下」那条判据）+ `_cost_of_one`（两条路共用的内核）」。

## 二、拆得开才拆（判据写死）

归并出来的**合计**必须与运行行那几列**逐个对得上**；对不上（HITL 续跑的第二段只有
累计用量，上一段的轮不在这一段的 `turns` 里）就**不拆** —— 退回单模型那条路，组合名
查不到价 -> 金额留空并写明原因，同时把前一段那笔按另一套单价算出来的金额**作废**
（C07 的 `void_cost`）。拆一半比不拆更糟：它看起来像个能对账的数。

## 三、展示

- 明细的每一行带上 `model` 与 `derived`（都是**可选键**：单模型那趟写出来的 JSON
  一字未变，老行读回来时 `derived` 回退到那一行整体口径 —— 兼容垫片在 `from_detail`）。
- `client/trace.py`：拆账那趟**每家一行算式**（`└ 主: ... ` / `  备: ...`）—— 不然后面
  那家的用量会被人按前面那家的单价验算，得出一个与金额不同的数。

## 四、验收

- [x] 一趟里两家都用过 -> 金额 = 两家各算各的和；明细逐行带归属
      —— `test_a_run_that_switched_models_is_priced_per_model`（手搓的轮）+
      `test_a_real_run_that_switched_between_models_is_priced_per_model`（**真跑一遍 loop**
      与真的包装，验「盖章真的走到 `LoopResult.turns` 里」这条缝）
- [x] 响应上盖的是**我们配的那家**（价目表的键），不是上游回显
      —— `test_the_response_says_which_model_answered` / `test_the_stamp_is_our_name_not_the_upstream_echo`
- [x] 有一家没配价 -> 整趟算不出来，且报的是**那一家**（定位到「谁的价没配」）
      —— `test_one_unpriced_model_makes_the_whole_run_unpriced`
- [x] 缺档推导按各家自己的输入总量推；「推出来的」标在**那一行**上
      —— `test_a_split_uses_each_models_own_input_total_for_the_missing_tier`
- [x] 拆不开那一路仍不硬拆（凑不齐 -> 留空 + 作废旧金额）
      —— `test_a_partial_split_is_not_priced`（原有那条「两段两家」的用例继续守着）
      · **跨段那一路已由 C24 补上**（逐模型用量落成一列），本票的边界自此收窄为「归并凑不齐」
- [x] 明细可往返（新键可选）、老行读回来形状不变
      —— `test_the_split_detail_round_trips_with_the_per_model_keys` +
      `test_a_derived_tier_is_marked_on_its_own_line`（老行回退那条）
- [x] 屏幕上每家一行算式
      —— `test_a_run_that_switched_models_prints_one_formula_per_model`
- [x] 现有用例全绿：框架 1482 passed / 132 deselected（+12）；CharApp 355 passed

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/retry/failover.py` | 成功返回时把服务方盖在响应上（`response.model`） |
| `CharAgent/db/recorder.py` | `_usage_by_model` + `_sum_field` + `_matches_run_totals`；`_cost_of` 增 `result=` 参数并按「拆得开吗」分派 |
| `CharAgent/db/cost.py` | `ModelUsage`；`CostLine.model` / `CostLine.derived`；`cost_of_split`；`cost_of` 拆成薄壳 + `_cost_of_one`；`from_detail` 对老 `derived` 形状的兼容垫片 |
| `CharAgent/client/trace.py` | `_formula_lines` / `_one_formula`：拆账那趟每家一行 |
| 测试 | `test_db_recorder.py`（+3：拆账、拆不开、端到端真跑）、`test_db_cost.py`（+5）、`test_retry_failover.py`（+2 盖章）、`test_client_trace.py`（+2 展示） |
| 文档 | `CharApp/docs/adr/0025`（决定 6 拆成 6/7 + 边界与替代方案改口径）· `CharAgent/docs/DESIGN.md` #14 · `docs/difficulties/02-stability.md` #14 · `CharApp/docs/PLAN.md` ADR 索引 · C07 的验收行 |

> 跨段那半（HITL 两段分属两家）见 **C24**：那一列 `usage_by_model` 落库之后，本票
> 「拆不开」这一句只剩「归并凑不齐」一种情形。

## 实施记录

- **没动库表、没动 `agent/loop.py`**：拆账的依据本来就在结果里（逐轮的响应带着 usage），
  缺的只是「谁答的」——那是模型层知道的，盖在响应上就够了。
- **`RunFacts` 刻意不收这一项**：它是「运行行的那几列」（`_write` 用 `asdict` 整包交给
  仓储），往里塞一个非列的字段会把 `settle()` 撑破（动手时真踩了一次）。逐模型用量是
  **算钱的中间件**，于是它只在 `_cost_of` 那一步从 `result` 现取。
- **对不上的判据逐个分量比**（input / output / hit / miss，None 与 None 算相等）：有一个
  对不上就整趟不拆。这条判据同时兜住了「模型没盖名字」（老装配）与「跨段只有半截」。
- **金额那一侧的老纪律一条没松**：算不出来仍不写 0、不给部分和；`derived` 的标记按行下
  沉之后，老行读回来的样子与当年屏幕上的一模一样（兼容垫片有专门一条用例）。
