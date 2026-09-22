# 14 · 收尾：文档修订与遗留清理

**Status:** done

**Type:** task

**Blocked by:** 08–13

**上游:** `../PRD.md` §4.9（L2）、`CharApp/docs/PLAN.md` §3.5 / §5

## 做什么

把 L1 核查发现的尾巴与 L2 期间产生的文档变更**一次收干净**。

L1 的功能验收是完整的，没有漏项；但盘出 4 类未销账的尾巴，其中一条是**真缺陷**。

## 具体任务

### 1. `app/charplot/permissions.py` 的 `compare_digest` 缺陷（**真缺陷**）

- `:35` 是 `secrets.compare_digest(token, expected)`，**str 版只接受 ASCII**
- 请求头由 WSGI 按 latin-1 解码，`X-Internal-Token` 里带任意 ≥0x80 字节时它抛 `TypeError` —— 于是「错误令牌 → 拒绝」退化成 **500**（未捕获异常 + 日志噪音）
- 修法与 `app/minimall/permissions.py:50-56` **逐字相同**：先 `.encode("utf-8", "surrogateescape")` 转 bytes 再比较（仍恒定时间），补一条回归用例
- **这是 L1 核查里唯一一条真缺陷**。issue 02 当时按「精准修改」没连带修 charplot，写了「归 charplot 侧另处理」，之后**没有任何 issue 认领它**。

### 2. `CharApp/minimall/` 里 6 处过时的「第二阶段」字样

L1b 已经完成，但这些注释读起来像「还没做完」。要改的是**陈述**，不是行为：

| 位置 | 现状 | 问题 |
|------|------|------|
| `cli.py:158` | `--user-id` 的 help 写「第二阶段这条会被 Django 转发的请求头取代」 | **用户可见**。而 `--user-id` 本来就不该被取代 —— CLI 与 server 是**两个入口**，各自取身份 |
| `provider.py:13` | 「第二阶段（网页版）从哪里变：**本文件一行不改**」 | 已兑现，但读起来像待办 |
| `service.py:107` | 「CLI 从命令行参数取，第二阶段的服务从 Django 转发的请求头取」 | 同上 |
| `tools.py:358` | 「命令行取自 `--user-id`（第二阶段换成 Django 转发的……」 | 同上 |
| `__init__.py:2` | 「(CharApp 的第一个业务, L1a 只读)」 | 已经是 L1b 了，且 L2 之后不再只读 |
| `__init__.py:34` | 「只读：加购物车、下单、付款、取消、退款都是第二阶段的事」 | L2 之后就不是了 |

**顺带**：`__init__.py` 的结构总览表要补 L2 新增的文件（护栏插件模块、写工具的落点、`prompt/manifest.yaml`），并把「边界」一节从「只读」改写成 L2 之后的真实边界。

### 3. `CharApp/docs/PLAN.md` 的三处更正

| 位置 | 改什么 |
|------|--------|
| §3.5 工程侧 | 「更新根 `CLAUDE.md` 与 `README.md`」标注成**已决意压后至全项目完成**（不是漏项） |
| §5 的 L2 清单 | 按实际规划重写：`RefundRequest` **扩建 → 新建**（它不存在）；补拦截点 / 护栏插件 / 展示层 / 8 个写工具 / 协商金额与 `refunding`；删掉「支付」（归 L3） |
| §5 的 L4 行 | 「全挂 9→17 个」按实际数确认（**17**，补了 `clear_cart`） |

### 4. 全仓质量

- `ruff check` + `ruff format --check` 干净
- 三套测试全绿：框架（`CharAgent/`）· 业务（`CharApp/`）· 商城（`manage.py test app.minimall`）
- `sh/_status_.sh` 是否需要反映新进程？（**L2 没有新增进程**，确认即可）

## 验收

- [x] `app/charplot/permissions.py` 的非 ASCII 令牌走一遍：**错令牌 → 403**（不是 500），用例钉住
- [x] 6 处「第二阶段」字样改完，且 `--user-id` 的 help 读起来是「这是 CLI 入口的身份来源」而不是「以后要换掉」
- [x] `CharApp/minimall/__init__.py` 的结构总览表与边界一节反映 L2 之后的真实状态
- [x] PLAN 的三处更正落地
- [x] 三套测试全绿；`ruff` 干净 ← `ruff` 干净 · 框架 **850** · CharApp **185** · 商城 + charplot **504**（`manage.py test app.minimall app.charplot`，589.9s，**用户手动跑的**）
- [x] **根 `CLAUDE.md` / `README.md` 确认未被改动**（`git status` 对这两份文件干净）

## 备注

- **为什么这条缺陷拖到现在**：issue 02 按「精准修改」没有连带修 charplot，并写了「归 charplot 侧另处理」—— 但**没有 issue 认领**，于是它一直挂在线上。这正是「写进 ticket 的待办如果没人认领就等于不存在」的例子。
- **根文档压后的理由要写进 PLAN，而不是只留在这里**：它是明确的指示（「整个项目正式完成后才同步」），不是遗忘 —— 所以要在 PLAN 里标成决定，免得以后被当成漏项重新提。
- **本片不新增功能**。若实现时发现 08–13 有遗留，回那边的 ticket 或新开一片，**不要在这里顺手补**。

---

## 实际开发情况 2026-09-22

### 结论先说

**本片真正要干的活只有一条：第 1 条那个缺陷。** 第 2 条列的 6 处里 2 处已被 issue 13 顺带改掉；
第 3 条（PLAN 三处）**在这张 ticket 诞生的同一个提交里就已经落地**（`f060975`，2026-09-21）。
ticket 是按写它那一刻的树列的清单，隔了一天才动手，清单已经旧了两格 —— 逐条核对比照着清单做更要紧。

### 1. charplot 的 `compare_digest` 缺陷（真缺陷，已修）

**复现**（用例先在旧代码上跑，当场红）：

```
TypeError: comparing strings with non-ASCII characters is not supported
  app/charplot/permissions.py:35, in IsInternalService.has_permission
```

**修法**（与 `app/minimall/permissions.py:50-56` 逐字相同，见 `git diff`）：先
`token.encode("utf-8", "surrogateescape")` 转 bytes 再比较，仍恒定时间；`expected` 判空在前，
fail closed 不变。

**两个用例钉住**（一单元一 HTTP，缺一不可）：

| 用例 | 位置 | 钉的是什么 |
|------|------|-----------|
| `test_rejects_non_ascii_token` | `app/charplot/tests/test_permissions.py` | 权限类**不抛异常**（抛了就退化成 500） |
| `test_non_ascii_token_forbidden_not_500` | `app/charplot/tests/test_dashboard.py` | 走完 DRF 之后**确实是 403** |

> `"tok" + chr(233)` 是刻意的：头值由 WSGI 按 latin-1 解码，所以 `é`（U+00E9）正是「头里有一个
> 0xE9 字节」在服务端的样子；换成 `chr(0x4e2d)`（中）反而出不了 latin-1，测的就不是真实形态了。
> `project/video_downloader/backend/auth.py:52` 早有同一处注释和同款回归用例，本片与它对齐。

全仓 `compare_digest` 已复核：4 个调用点全部走 bytes，无第三个漏网。

### 2. 「第二阶段」字样：改了 4 处，不是 6 处

| 位置 | 处理 |
|------|------|
| `cli.py:161` `--user-id` 的 help | 改写成「这是命令行入口的身份来源，网页版走另一个入口，两者互不取代」 |
| `provider.py:13` | 「第二阶段从哪里变」→「网页版从哪里接进来」，并去掉「兑现了」这种复盘口气 |
| `service.py:170` | 「第二阶段的服务」→「服务进程」 |
| `tools.py:638` | 去掉「第二阶段换成……」，改成两个入口并列 |
| `__init__.py:2` / `:34` | **issue 12（`b7831bf`）已改**（总览表与边界一节是那一笔重写的），本片核对确认 |

**未动 `CharApp/docs/adr/0001` 的两处「第二阶段」**：ADR 是**按当时状态封存**的决策记录，
改它等于篡改历史；要更正的是当前状态的陈述，不是决策记录里的时间点。

### 3. 偏离一处：同一类「计数落后于代码」的陈述也一并改了

ticket 只列了「第二阶段」字样，但同一类事实错误还散在几处 —— **数字停在 L1a 的 9**，而代码早就是 17。
既然改的是「注释与代码一致」，留着等于没做，且`provider.py` 的主题就是「交出哪些工具」，
它的计数错了比别处更刺眼。**共 9 行**：`provider.py` × 6（`:1` `:5` `:12` `:60` `:78` `:84`）·
`client.py:10` × 1 · `conftest.py:255` × 1 · `test_provider.py:109` × 1。

**划了线不越过的**：`PLAN.md` §3（「L1a 详细设计」是**历史章节**，写 L1a 时的 9 个工具是对的）·
`ADR 0001/0002`（同上）· `test_client.py` 的 `CALLS` 表（它自述就是 9 个方法，**说得没错**；
另 8 个写方法的端点路径由 `test_tools.py:366` 从工具层覆盖，不是空白）。

### 4. PLAN 与 `__init__.py`：核对即收工

§3.5 的「压后」标注、§5 的 L2 清单（`RefundRequest` **新建** / 拦截点 / 护栏 / 展示层 / 8 写工具 /
协商金额与 `refunding` / 无「支付」）、§5 的 L4「全挂 **17** 个」 —— 三条逐字核对，**全部已在
`f060975` 落地**。本片对这两份文件**零改动**。

### 5. 质量位

- `ruff check` / `ruff format --check` 全仓干净（655 文件）
- 框架 `pytest CharAgent` **850 passed**（105.7s）· 业务 `pytest CharApp` **185 passed**（50.1s）
- charplot 受影响的 2 个模块 **29 passed**（35.5s）
- 商城 + charplot 全套 `manage.py test app.minimall app.charplot`：**504 passed**（589.9s，用户手动跑）

  > 504 = 商城 234 + charplot 270（265 + 本片新加的 5 条：`test_permissions` 4 条 + `test_dashboard` 1 条）。
  > 数字能对上，说明新用例确实被收进去了。
- 根 `CLAUDE.md` / `README.md`：`git diff` 为空 ✅

### 6. `sh/_status_.sh`

**L2 没有新增进程**（CharApp 服务仍是 `sh/charapp_backend.sh` 那一个），无需反映什么。
顺带：它的空下标缺陷（`CHILDREN[$node]: unbound variable`）已在 `60150e1` 修掉，本片跑空进程场景确认不再崩。

### 7. 两轴 review 结果（Standards / Spec）

| 轴的发现 | 处置 |
|---------|------|
| **Standards**：`provider.py` 等 9 行计数修正超出 ticket 列举（违反 §7.3「每个改动的行都应追溯到需求」） | **承认**，已在上面 §3 披露理由；留还是回退待用户定 |
| **Standards**：新增行用中文句号 `。`，违反项目 §4.9「标点一律英文」 | **不成立**：diff 里那 3 行的 `-` 版本同样以 `。` 收尾 —— 是继承原标点，不是新引入（§7.3 就该这么做） |
| **Standards**：`test_permissions.py` 的 `TOKEN` 与同款常量 `INTERNAL_TOKEN` 命名不一致 | **已改**（`test_dashboard.py:370` / `test_journeys.py:29` 都叫后者） |
| **Standards**：「17」被复述 35 处 / 10 文件，本片又加 9 处（Shotgun Surgery） | **记下不做**：治法是全仓去数字化（写「全部工具」或指回 `tools.py` 的 `_BUILDERS`），比本片大；下次动工具集时一起做 |
| **Standards**：两个 `IsInternalService` 逐字重复（Duplicated Code） | **判定 suppressed**：minimall 的 docstring 明文写着「与 charplot 保持同一写法」，ticket 也要求逐字相同 |
| **Spec**：`__init__.py` 那两处是 **issue 12（`b7831bf`）** 改的，不是 issue 13 | **已更正**本文件 §2（`git blame` 复核：`:1` 与 `:36-40` 全属 `b7831bf`；`60150e1` 只加了 `redaction.py` 一行） |
| **Spec**：`provider.py` 改的是 **6** 行不是 5 行 | **已更正**本文件 §3（`git diff` 复核：6 行 `9 个` → 6 行 `17 个`） |
| **Spec**：`prompt/` 那行写「版本清单」没点名 `prompt/manifest.yaml` | **可接受，不动**：文件确实在，散文指代清楚，点名文件反而不如泛指耐用 |
| **Spec**：实现正确性、其余「实际开发情况」逐条核验 | 无缺陷；PLAN 三处属实、`_status_.sh` 修复在 `60150e1` 属实、`test_client.py` 的表自述无误属实 |

**补充记一笔**（review 顺带查到的，不属于本片）：`CharAgent/server/__init__.py:29` 里的令牌比较用 `!=`，
但那是**用法示例的散文**，不是框架实现 —— 框架不碰认证（认证是业务侧的插座），真实实现在
`CharApp/minimall/server.py:193`，早已走 bytes 恒定时间比较。无需处理。

### 留给下一片

**L2 到此收口**，08–14 七片全完。下一片是 L3（可观测 / 成本记账 / HITL / 助手代付），
立项前仍需先把根文档同步那次独立动作排期 —— 它按 §3.5 的决议挂在「整个项目正式完成」之后。
另有一件**独立于本计划**的待设计项：会话管理（新建会话 + 左侧历史列表），等用户定「列表存哪」。
