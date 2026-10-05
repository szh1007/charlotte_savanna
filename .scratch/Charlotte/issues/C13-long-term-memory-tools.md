# C13 · 长期记忆-b：`remember` / `recall` 工具

**Status:** done

**Type:** feature

**Blocked by:** C12

**上游:** `CharAgent/docs/DESIGN.md` §4 ④ 的 #33（「谁决定写、写什么；加权取 top-k；衰减与容量淘汰」）；`.scratch/Charlotte/PLAN.md` §6.3 的 MEM-D1

## 设计取舍（先说这个，它决定了整票的形状）

**「谁决定写」是 #33 的真问题。** 三条路：规则抽取（每轮结束按模板抽）/ 模型主动调工具 / 后台异步抽取。你选了**模型主动**。

它的好坏都要能讲：

| | |
|---|---|
| **好处** | 模型知道「这句话以后还用得上」—— 规则抽不出来；工具形态**能演示**（面试时看得见模型主动记了一笔） |
| **代价** | 写多了污染（记一堆没用的）；写少了没用（该记的没记）。**缓解手段是容量上限 + 衰减淘汰**（C12 已建），而不是靠 prompt 求它节制 |

**另一个决定**：`recall` **不带 `query` 参数**。理由：条数有上限（C12 的 N），全部按衰减排序返回就够 —— 带 `query` 就需要向量或关键词匹配，而那是**记忆量大到「列不完」时**才成立的优化。**这个边界要写在模块 docstring 里**：什么时候该加 `query`（当单个用户的记忆条数逼近上限、全量返回开始挤占上下文时）。

---

## 一、框架侧：工具工厂（`CharAgent/tool/`）

```python
def build_memory_tools(
    repository: MemoriesRepository, *, tenant_id: str, user_id: str
) -> list[Tool]:
    """返回 [remember, recall] 两个工具，身份由闭包持有。"""
```

- **身份不进参数表**（对齐 `CharApp/minimall/tools.py` 的约定 ①⑤）：`tenant_id` / `user_id` **走闭包**，因此永不出现在暴露给模型的 wire schema 里 —— 模型既看不见也改不了「记给谁、读谁的」
- `remember(content: str, kind: Literal["episodic", "semantic"]) -> str`
  - 描述要写清「**什么值得记**」（跨会话还有用的事实与偏好）与「**什么不要记**」（这一轮查到的订单号、余额 —— 那些下次自己查得到）
  - 长度上限；**完全相同的 `content` 走「更新时间戳」而不是新增**（去重）
  - **疑似敏感值拒绝写入**（支付密码那类）—— 可以复用 `CharAgent/redact/` 的规则做检测，但语义不同：`redact` 是**打码**，这里是**拒绝**
- `recall() -> str` —— 按衰减分值排序返回 top-N（N 取 C12 的上限），每条带 `kind` 与时间

## 二、业务侧：装配

- 在 `CharApp/minimall/tools.py` 的 `build_tools` 里挂上（**不放进 `_BUILDERS` 表** —— 那张表的签名是 `(client, user_id) -> Tool`，而记忆工具要 repository）。参考 `_pay_my_order` 单独 `append` 的既有做法（`tools.py:783`），理由同样写在 docstring 里
- repository 从哪来：与 `MinimallToolProvider` 的既有装配同一处（`provider.py:110-126`），身份从 `RunContext.payload` 取（`buyer_id(context)` 现成）
- `CharApp/tests/conftest.py` 的 `TOOL_NAMES` **权威清单要加上这两个名字**

## 三、prompt v6

> **票面这一节写于 C11 之前 —— 版本号已漂**：`v6` 被 C10（逐句引用）占用、
> `v7` 被 C11（注入防护）占用，实际开的是 **`v8`**（2026-10-05 落地，下文按票面
> 原样保留、偏差记在「实施记录」）。

新建 `CharApp/minimall/prompt/system/v6.prompt`（不覆盖 v5），加一节：

- 什么时候 `recall`（**对话开始时如果有需要** —— 别每轮都调）
- 什么时候 `remember`（买家说了一个**跨会话还有用**的事实或偏好）
- 什么不要记（这一轮的订单号、余额）

`manifest.yaml` 的 `default` 切 `v6`，注释里记一行为什么切。

> 这个项目的 prompt 版本化成本极低（一版一个文件 + manifest 一行）—— **不要因为"又开一版"而犹豫**，那正是这套机制存在的意义。

---

## 验收

- [x] **跨会话**：会话 A 里说「我以后都想要货到付款」→ **新开一个会话**问「我有什么偏好」→ 答得出
      —— 真机两条 CLI 会话（`mem-a` 记下 → `mem-b` 全新会话答出「你之前说过偏好货到付款」）；
      离线用例 `test_a_memory_survives_a_new_conversation`（换新装的一套工具，同一份替身）
- [x] **否定断言**：换一个买家账号 → `recall` 里**看不到**上一个人的记忆
      —— 真机（买家 9999 的 `recall` 回空）+ `test_another_buyer_or_tenant_cannot_recall_the_memory`
      （买家 / 租户两个方向）+ C12 真库用例
- [x] 敏感值（支付密码形状）写不进记忆，`remember` 返回可操作的拒绝文本
      —— 框架 `test_a_sensitive_value_is_rejected`（手机号 / 6 位数字 / 邮箱 / 证件号 / 银行卡
      五形状参数化）+ `test_a_long_order_number_is_not_a_sensitive_value`（24 位订单号不误伤）
      + 业务侧 `test_a_sensitive_value_never_reaches_the_store`
- [x] 同一句话连续 `remember` 两次 → 不产生两条
      —— C12 真库用例（`updated_at` 刷新而 `created_at` 不动的边界也钉了）+
      `test_repeating_the_same_thing_does_not_store_it_twice`；**真机发现一处边界**（见实施记录）
- [x] `recall` 的返回顺序符合衰减分值
      —— `test_recall_returns_the_newest_first` + 框架侧保序用例（顺序由仓储给，工具只保序）
- [x] 两个工具的 wire schema 里**不含** `tenant_id` / `user_id`
      —— 框架 `test_neither_tool_exposes_an_identity_parameter`（键名 + 值两层搜）+
      `test_provider.py` 的 `EXPECTED_PARAMS` 逐个钉死（`remember` = {content, kind}，`recall` = ∅）
- [x] 超过容量上限时旧记忆被软删，`recall` 不再返回它
      —— `test_going_over_capacity_evicts_the_oldest`（capacity=2 写 3 条，行还在、`deleted_at` 有值）
      + C12 真库用例
- [x] `trace <run_id>` 里能看到 `remember` / `recall` 的调用与耗时
      —— 真机：`recall 73ms {}` / `remember 73ms {"content": "偏好货到付款", "kind": "semantic"}`，
      提示词那一行是 `system/v8`

## 开工前要定的

- 「什么值得记」在 prompt 里的措辞（这一节写得越具体，写入质量越好；建议给 3–5 个正例与 3 个反例）
  —— **定了**：v8 的「长期记忆」一节给 4 个正例（寄到公司 / 货到付款 / 家里有小孩 / 上周申请过退款）
  与 4 类反例（订单号余额验证码 / 临时上下文 / 自己的推测 / 敏感值形状）；工具自身的 docstring
  里各给一组（模型看的是它）
- 是否给 `remember` 加一个「重要性」参数让模型自评（**建议先不加** —— 参数越少越容易被正确调用，这是 #70 的第三条 tradeoff）
  —— **不加**（票面建议，照做）：`remember` 只有 `content` + `kind` 两个参数

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/tool/memory.py` | **新写**：`build_memory_tools`（remember / recall）+ `MAX_CONTENT_LENGTH` + `SENSITIVE_PATTERNS`（复用 redact 的正则 + 一次性密码那条）+ 拒写文案 |
| `CharAgent/tool/__init__.py`、`CharAgent/__init__.py` | 门面导出 `build_memory_tools` / `MAX_CONTENT_LENGTH`（防漂移用例过） |
| `CharAgent/tests/test_tool_memory.py` | **新写**：13 条（顺序 / schema 无身份 / 透传 / 五形状拒写 / 订单号不误伤 / 超长 / recall 格式与保序 / 空记忆） |
| `CharApp/minimall/provider.py` | 构造加 `memory`（可空）；`provide` 把记忆工具接在**最后**，身份取 `context.tenant_id` / `context.user_id` |
| `CharApp/minimall/service.py` | `_memory_store()`（**真库才有记忆**，跑分因此天然不装）+ `session_for` 接上 |
| `CharApp/minimall/redaction.py` | `TOOL_PHRASES` 加 remember / recall 两行（与 `TOOL_NAMES` 的一一对应由既有用例守） |
| `CharApp/minimall/scoping.py` | `TOOL_GROUPS` 加 `memory` 组（并集 == 清单那条不变量）+ `GROUP_KEYWORDS` |
| `CharApp/minimall/prompt/system/v8.prompt` + `manifest.yaml` | **新写**：v7 + 能力表两行 + 「长期记忆」一节 + 示例两则（v7 逐字可 diff）；`default: v8` |
| `CharApp/tests/conftest.py` | `TOOL_NAMES` 加两名；`FakeMemoryStore`（契约四面最小复刻）+ `memory_for_tests` |
| `CharApp/tests/test_tools.py` | `tools_of` 升级（含记忆、与 provider 同形）；三条遍历用例改走它；记忆一节 +6 条 |
| `CharApp/tests/test_provider.py` | `provider_for` 加 memory；`EXPECTED_PARAMS` 加两名；无参数工具数 7→8 |
| `CharApp/tests/test_prompt.py`、`test_eval_subject.py` | v7 基线 + 当前版本切 v8；跑分工具数改成「清单 − `EVAL_SKIPPED_TOOLS`」+ 新用例「跑分不装记忆」 |
| `CharApp/eval/golden.py`、`CONTEXT.md`、`docs/PLAN.md` | `_real_tool_names` 的注释说明；「长期记忆」词条；L5 段补 C12+C13 落地记录 |

## 实施记录

**三处与票面的偏差，都记在这里**：

1. **prompt 是 v8 不是 v6**（票面写于 C10/C11 之前）：v6 被逐句引用占用、v7 被注入防护
   占用 —— 实际新开 `v8.prompt`（v7 逐字 + 能力表两行 + 「长期记忆」一节 + 示例两则）。
   加示例不在票面的「加一节」里，但新能力不给示例模型不会主动用（与 v3 加下单/付款
   那两则同款）；三处改动都记在 `manifest.yaml` 的注释里。
2. **装配在 `provider.provide` 里接，不在 `tools.build_tools` 里**（票面点名了后者）：
   记忆的身份是 `(tenant_id, user_id)` 那一对**会话隔离键**，只有 `provide` 手里有
   `context`；`build_tools` 的入参是业务身份 `(client, int user_id)`，装不下它。票面
   那句「不进 `_BUILDERS` 表、单独接」的精神未变（接在最后一位），落点换了。副作用
   是 `tools_of`（测试装配）跟着升级成「与 provider 同形」，顺序由 `TOOL_NAMES` 守。
3. **跑分那条线不装记忆**（票面没提，实施中发现的岔口）：跑分**逐题独立**，而记忆是
   状态性的（一题写下的偏好，下一题读得到）—— 装了就会让题与题互相污染，A/B 的两组
   也不再逐题可比。判据落在 `service._memory_store`：**库入口是不是真库**
   （`PgDatabase`）—— 跑分用 `FakeRecordDatabase`（记录层替身），于是天然没有记忆。
   这是跑分与生产**唯一**的工具差，有一条用例守着（`test_the_eval_line_does_not_wire_the_memory_tools`）。

**敏感值拒写用的是宽口径**（票面只点名「支付密码那类」）：redact 认得的四类 PII
（手机号 / 邮箱 / 证件号 / 银行卡）+ 一次性密码那条（6 位数字）。理由是记忆会在以后
**每一段对话**里进上下文——PII 长期留在提示词里既没必要（档案工具现查得到）也有
出站成本（发给模型供应商）。与 `redact/` 的语义分工写进了模块 docstring：那边是
「打码之后照写」，这边是「拒绝写入」。

**真机一条边界（如实记）**：精确匹配的去重**不容易被触发** —— 模型每次把同一条偏好
提炼的措辞可能不同（真机上「以后我的东西都想要货到付款」第一次记成「偏好货到付款」，
第二次记成「偏好货到付款（东西都要货到付款）」→ 落成两条）。这是 C12 就写明的取舍
（不做相似度：判错的代价比多留一条大），真机把它变成了看得见的事实。要收紧的话抓手
是「按 kind + 关键词的近似去重」或让 `remember` 先 `recall` 对比——都留给将来。

**真机**（2026-10-05，买家 10，CLI 与浏览器两侧都跑了）：

- **CLI 四条**：跨会话（`mem-a` 记 → `mem-b` 全新会话答出）；隔离（买家 9999 的
  recall 回空）；trace（`recall 73ms {}` / `remember 73ms {"content": "偏好货到付款",
  "kind": "semantic"}`，提示词 `system/v8`）；库里落行（租户 `minimall-cli` 两条）。
- **浏览器端两条**（`/minimall/agent/`，真页面 + 真模型）：说「我以后的东西都寄到公司
  地址，记住这个偏好」→ 页面工具事件渲染成「这件事记下了」，答复「好，记住了」；
  点「+ 新对话」开**全新会话**问「你还记得我有什么偏好吗」→ 事件「想起来了」+ 答复
  「你之前说过：以后收货都寄到公司地址」—— 网页租户（`minimall`）的跨会话成立。
  库里三行两桶分明（`minimall` 一条 / `minimall-cli` 两条 —— CLI 与网页的记忆按设计
  互不可见）。
- **敏感值真机两次都停在前一道闸**：说「记住我的手机号 13800000003」与「以后寄东西
  就写这个联系方式 13800000003」，模型都在**提示词那一层**就拒绝（第一次连工具都没调，
  第二次调了 `recall` 后直接回「这个我记不了 …可以去个人中心的收货地址里改」）——
  工具层的拒绝写入是**兜底那道**，真机上没被走到；它的行为由离线用例钉死
  （五形状参数化 + 仓储零调用 + 24 位订单号不误伤）。

**code-review 后修的两处**（2026-10-05）：替身 `FakeMemoryStore._prune` 的切片在
「未满容量」时会把最老的几条误删（`alive[: len(alive) - capacity]`，len < capacity
时负切片从尾部数）—— 当时的用例写得太少（1~3 条）恰好踩不到，补了一条「未满不淘汰」
的对照用例把这一格钉住；`SENSITIVE_PATTERNS`（跟着 redact 自动长）与 `_SHAPE_LABELS`
（手写映射）之间补了一条一一对应的守卫用例（redact 加第五条规则时当场红，与
`TOOL_PHRASES` 那条同款）。另有一处**如实记录的错配**：跑分那条线的提示词仍是默认
v8（里面有「先调一次 recall」），而它的工具集没有记忆工具 —— 与 scoping 裁剪组
「提示词提到、工具没给」同类，影响有界（模型只会调 wire 上真有的工具），记在
`service._memory_store` 的 docstring 里。
