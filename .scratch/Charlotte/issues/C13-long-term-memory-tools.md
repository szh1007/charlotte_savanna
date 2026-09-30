# C13 · 长期记忆-b：`remember` / `recall` 工具

**Status:** todo

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

新建 `CharApp/minimall/prompt/system/v6.prompt`（不覆盖 v5），加一节：

- 什么时候 `recall`（**对话开始时如果有需要** —— 别每轮都调）
- 什么时候 `remember`（买家说了一个**跨会话还有用**的事实或偏好）
- 什么不要记（这一轮的订单号、余额）

`manifest.yaml` 的 `default` 切 `v6`，注释里记一行为什么切。

> 这个项目的 prompt 版本化成本极低（一版一个文件 + manifest 一行）—— **不要因为"又开一版"而犹豫**，那正是这套机制存在的意义。

---

## 验收

- [ ] **跨会话**：会话 A 里说「我以后都想要货到付款」→ **新开一个会话**问「我有什么偏好」→ 答得出（这一条是「长期」二字的全部意义）
- [ ] **否定断言**：换一个买家账号 → `recall` 里**看不到**上一个人的记忆
- [ ] 敏感值（支付密码形状）写不进记忆，`remember` 返回可操作的拒绝文本
- [ ] 同一句话连续 `remember` 两次 → 不产生两条
- [ ] `recall` 的返回顺序符合衰减分值
- [ ] 两个工具的 wire schema 里**不含** `tenant_id` / `user_id`（`CharApp/tests/test_provider.py` 有同款的 schema 断言可照抄）
- [ ] 超过容量上限时旧记忆被软删，`recall` 不再返回它
- [ ] `trace <run_id>` 里能看到 `remember` / `recall` 的调用与耗时

## 开工前要定的

- 「什么值得记」在 prompt 里的措辞（这一节写得越具体，写入质量越好；建议给 3–5 个正例与 3 个反例）
- 是否给 `remember` 加一个「重要性」参数让模型自评（**建议先不加** —— 参数越少越容易被正确调用，这是 #70 的第三条 tradeoff）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
