# C24 · 逐模型用量落库：HITL 跨段也拆得开

**Status:** done

**Type:** feature

**上游:** `CharApp/docs/adr/0025`（决定 7 的后半）；C23（段内拆账）

## 现状（开工前读码核实）

C23 做完之后，「一趟里换过家」只剩一种形态算不出钱：**HITL 跨段**。

| 事实 | 证据 |
|---|---|
| 续跑那一趟的 `turns` **只含本段的轮**（计数器却是累计的） | `agent/loop.py` 的 `resume` docstring：「turns 只含本次的轮次, turn_count 是含续跑前轮数的累计值」 |
| 快照里**没有**每轮的响应 | `CheckpointState` 的字段表：messages + 计数器 + 摘要 / 引用，没有 turns |
| 段 1 写进库里的只有**累计五列**（不分家） | `runs.settle` 写的就是那五列 |
| 于是段 2 只能拿到「整趟累计」+「本段那几轮」—— 余量（累计 − 本段）归不了因 | 段 1 若自己就混过家，行上那个名字是组合名，一坨 token 分不了比例 |

**唯一能补救它的时刻是段 1 收尾那一刻**（那时它手里有本段的逐轮响应）—— 所以这一片做的是：
**把「哪一家产出了多少」落成一列**。

## 决定（三条口径写死在代码注释与 ADR 里）

1. **`charagent_runs.usage_by_model`（JSONB，可空）**：形如
   `[{"model": ..., "input_tokens": ..., "cache_miss_tokens": ..., "cache_hit_tokens": ..., "output_tokens": ...}]`，
   按模型名去重、按首次出场排序；**缺的分量不写键**（与 `RunCost.to_detail` 同一条规矩）。
   NULL = 没有可归因的逐模型用量（老行 / 一段里一次模型调用都没有 / 归并不齐）——
   与「空列表 = 确认过一家都没产出」不是一回事。
2. **整份覆盖，不是本段增量**：记录员把「列上那份 + 本段那份（按模型名相加）」合并好
   再整份交出去 —— 与金额那两列「算不出来就不动」的口径**不同**（那一列是累计事实的
   快照，金额是那一刻的一次折算）。
3. **只在归并结果与运行行那几列对得上时才写**：对不上就**不写也不拆** —— 金额留空 +
   写明原因 + 作废旧金额（C07 的 `void_cost`）。现实里对不上的只有一种：那一列是 NULL
   （升级前建的运行行、升级后续跑）；判据本身不看「为什么对不上」，它只是一道闸。

## 验收

- [x] 段内混过家的一趟：那一列记着两家的分量（按首次出场排序）
      —— `test_a_mixed_segment_leaves_its_per_model_usage_on_the_row`
- [x] **跨段**：段 1 自己就混过家（组合名）+ 段 2 由其中一家接着答 -> 合并之后
      **按各行分别算钱**（这正是「余量归因」推不出来的那一格）
      —— `test_a_resumed_segment_merges_with_the_column_and_prices_both`（0.0056 + 0.00451 = 0.01011）
- [x] 归并凑不齐：不算钱，也不把半份写回列里
      —— `test_a_column_that_does_not_add_up_is_neither_priced_nor_rewritten`
      （C23 的 `test_a_partial_split_is_not_priced` 继续守着同一件事的另一面）
- [x] 那列读不懂时当没有（兜底，不是「情形」）：不抛、不算、不写
      —— `_usage_from_column` 的容错解析（与 `RunCost.from_detail` 同一条态度：
      收尾这条路上少一笔金额，比让整轮记不上账轻）
- [x] 迁移与表定义零差异、可回退、版本表记到 0006
      —— `pytest -m pg_db`：98 passed（含 `alembic check` 的零差异门与
      `test_the_version_row_records_the_step_that_just_ran` 的六步审计）
- [x] 现有用例全绿：框架 1485 passed / 132 deselected（+3）；`-m pg_db` 98 passed

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/db/schema.py` | `usage_by_model` 列 + 注释常量（与迁移逐字一致） |
| `CharAgent/alembic/versions/0006_run_usage_by_model.py` | **新**：加列 / 回退 |
| `CharAgent/db/entities.py` | `Run.usage_by_model` 的说明 |
| `CharAgent/db/repositories/runs.py` | `_params` 加键、`add` 显式 None、`settle(usage_by_model=)`（整份覆盖） |
| `CharAgent/db/recorder.py` | `_usage_by_model`（改成「本段全部轮」，不再带「够不够」的判据）· `_merged_usage` / `_sum_option` / `_usage_from_column` / `_usage_rows` · `record()` 里合并 + 验证 + 落库 · `_cost_of(usage=)` |
| 测试 | `test_db_recorder.py`（+3）、`test_db_alembic.py`（head 版本号 / 步数 / 标题跟到 0006） |
| 文档 | `CharApp/docs/adr/0025`（决定 7 补跨段 + 边界与替代方案改口径） |

## 真机验证（2026-10-05，环境修好之后）

- **单模型 failover**（主模型 base_url 指到连不上的端口 → 连挂 3 次跳闸）：备份 `gpt-6-luna`
  答完并**真的调了工具**（余额 19802.00）；那一趟的行：`model=gpt-6-luna`、
  `usage_by_model=[{gpt-6-luna, in=12403, out=41, hit=6164}]`（这家不报未命中，键缺省）、
  金额 `0.007060` = 6239x¥1/M（推自 input）+ 6164x¥0.1/M + 41x¥5/M ✓
- **组合名那种**（本地桩扮主模型答第一轮并调真工具，随后连挂 3 次 → 真备份答完）：
  `model=deepseek-flash+gpt-6-luna`、`usage_by_model` 两条、
  金额 `0.002338` = 主 0.001400（谷价 1/0.02/4）+ 备 0.000938（1/0.1/5，未命中推自 input）✓
  一行不差；`trace` 上**每家一行算式** ✓
- **真机抓出的一个缺陷**（已修）：判据原先要四个分量全对得上，而真机两家上报口径不同
  —— 组合名那趟会**永远验不过**。改成只比 `input` / `output`（两家都必报；少一段必然让
  输入变小，所以「缺一段」照样拦得住）+ 一条对应用例
  （`test_a_vendor_that_does_not_report_cache_miss_still_prices`）。
- **同一天又补一处**（用户指出）：未命中那一档原先只有**金额**会推（`db/cost.py`），
  运行行那五列记的是「已上报部分的和」—— 真机上出现「输入 7468 / 未命中 1000 / 命中
  6233」这种行（金额按 1235 收费、账上只记了一半）。改法：**在解析层就补齐**
  （`model/parse.py` 的 `_derive_cache_miss`，输入 - 命中，推不出来就不补），于是累加 /
  `usage_by_model` / 金额三处看到的是同一份完整的账；`db/cost.py` 的推导保留作老数据兜底。
- **一处已知不覆盖**：跑过上下文压缩的运行（摘要那一次调用的用量不属于任何一轮）会被
  那道闸拒 → 金额留空；记进 ADR 的边界，真机上暂未遇到。

## 实施记录

- **为什么是列而不是「借道 `total_cost_detail`」**：段 1 若是混用，它算出来的 `lines`
  本来就逐行带着 model 与 tokens，第二段读回来即可 —— 能省一次迁移。否掉的理由写在
  ADR 的替代方案里：那列说的是「这笔钱怎么算的」，把用量账塞进去，下一个人得先绕过
  这个弯；而且它只对「段 1 算出了金额」的行有效。
- **为什么不改 `settle` 的「重算」口径**（改成「段 2 交增量 + 金额累加」）：同一次 run
  的 resume 可能被点两次（HTTP 重发 / 用户重试），累加语义下金额会翻倍；重算语义下
  同一份累计值算出来还是同一个数，天然幂等。ADR 的替代方案里记了这一条。
- **那一列不进 `RunFacts`**：`RunFacts` 是「运行行的那几列」（`_write` 用 `asdict` 整包
  交给仓储），它是**派生数据**（收尾那一刻算出来的），于是单独走 `RunSettlement`。
- **判据只有一条**：`_matches_run_totals`（四个分量逐个比）既管「要不要拆」也管
  「要不要写回去」—— 一条判据守两件事，比两条各自漂的判据好守。
- **动手时顺手把 C23 的边界收窄了**：`_usage_by_model` 从「判够不够」退回成「只归并本段」，
  「够不够」整个交给合并之后那条判据 —— 一份数据两种用途（本段归并 / 整趟验证）分开走。
