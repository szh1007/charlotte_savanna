# 14 · 收尾：文档修订与遗留清理

**Status:** ready-for-agent

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

- [ ] `app/charplot/permissions.py` 的非 ASCII 令牌走一遍：**错令牌 → 403**（不是 500），用例钉住
- [ ] 6 处「第二阶段」字样改完，且 `--user-id` 的 help 读起来是「这是 CLI 入口的身份来源」而不是「以后要换掉」
- [ ] `CharApp/minimall/__init__.py` 的结构总览表与边界一节反映 L2 之后的真实状态
- [ ] PLAN 的三处更正落地
- [ ] 三套测试全绿；`ruff` 干净
- [ ] **根 `CLAUDE.md` / `README.md` 确认未被改动**（`git status` 对这两份文件干净）

## 备注

- **为什么这条缺陷拖到现在**：issue 02 按「精准修改」没有连带修 charplot，并写了「归 charplot 侧另处理」—— 但**没有 issue 认领**，于是它一直挂在线上。这正是「写进 ticket 的待办如果没人认领就等于不存在」的例子。
- **根文档压后的理由要写进 PLAN，而不是只留在这里**：它是明确的指示（「整个项目正式完成后才同步」），不是遗忘 —— 所以要在 PLAN 里标成决定，免得以后被当成漏项重新提。
- **本片不新增功能**。若实现时发现 08–13 有遗留，回那边的 ticket 或新开一片，**不要在这里顺手补**。
