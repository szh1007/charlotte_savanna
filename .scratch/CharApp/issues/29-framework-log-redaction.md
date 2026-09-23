# 29 · 日志脱敏：先堵三个实证泄漏点，再给通用规则

**Status:** todo

**Type:** task

**Blocked by:** 无（可与其他片并行）

**上游:** `CharAgent/docs/DESIGN.md` #26（敏感信息脱敏：**日志进之前先脱敏**；按字段类型脱敏而非正则碰运气）· #38（结构化日志 + 全链路关联）；`CharApp/docs/PLAN.md` §5 的 L3a 段（验收："构造一条含手机号的日志，落盘后搜不到原文"）

## 先修正一处预期：靶子不在框架侧

规划时写的落点是"框架给 `Redactor` 协议 + 通用规则"。**核实之后发现，已知的泄漏点三个里有三个都不在 `CharAgent/` 里** —— 框架侧 5 个模块共 11 处日志调用，**全是 warning/error，没有一处打过用户数据**（唯一的弱证据是 `agent/compaction.py:861` 用 `%r` 打异常，异常文本可能带上游错误信息）。

所以本片的顺序反过来：**先堵实证的洞，再给通用机制**。否则就是"给一个没有病人的地方建医院"。

## 一、三个实证泄漏点（按严重度排）

### ① 搜索词进 CharApp 的**访问日志**（URL 里的用户数据）

`app/minimall/views_bff.py:1030` 把搜索词作为 `?q=` 拼进上游 URL（常量 `:132-135`），而 CharApp 用 uvicorn `log_level="info"` 起（`CharApp/minimall/server.py:347-349`，**access_log 未关**）—— 访问日志会把**整条 URL** 记下来。

**反差最能说明问题**：前端 `templates/minimall/agent.html:1120-1122` **特意**把搜索词放进**请求体**，并注明理由「它完全可能是一个订单号」。前端守住了，后端在最后一跳漏了。

**修法（推荐 a）**：

| # | 做法 | 代价 |
|---|------|------|
| **a** | **关掉 CharApp 的 uvicorn access log**（`server.py:347-349`） | 砍掉一层观测。**但要如实说清**：access log 记的是"每条请求的 URL 与状态码"，而本项目的 URL **可能带用户数据** —— 关它是一次性的、覆盖**所有**将来带进 URL 的数据，而不是打补丁。失去的观测由 L3a 的 `trace`（issue 28）与业务自己的日志补 |
| b | 搜索词改走请求体（动 CharApp 的端点契约） | 要动 Django 与 CharApp 两侧的协议，而 URL 里**还可能**出现别的东西（会话编号、run_id）—— 治标 |
| c | 自定义 access log 过滤器只打印 path 不打印 query | 保住了观测、去掉了值。代价是要写 asgi 中间件或 uvicorn 的 log config —— 而 path 里的会话编号仍然带买家段（`:471` 已经在别处打过它的 `%r`） |

### ② Django 侧把**上游返回正文**截 200 字符写进日志

`app/minimall/views_bff.py:970`（同类 `:832`、`:1114`）：

```python
logger.warning("%s: 客服服务拒绝了这次%s: HTTP %d %s", who, action, status, _detail(response))
```

`_detail`（`:354-366`）把上游响应体截到 200 字符，而 `_DETAIL_LIMIT`（`:203`）的注释直接写着「**只进日志**」—— 也就是说这段正文是**专门为了进日志才留下的**。里面是工具的返回或业务的拒绝文案，含订单号、地址、余额。

**修法：只记状态码与上游的错误码，不打正文。** `_refusal_code(response)`（`:368`）已经从响应里取出了结构化的错误码 —— **有码就够定位了**，正文是"顺手多记的"。

**为什么这里不用"按字段脱敏"**：那是**第三方返回的正文**，我们**没有它的字段知识** —— 不知道第 137 个字符是订单号还是商品名。在没有字段知识的地方硬做正则，正是 #26 说要避免的那种碰运气。

### ③ 模型重试提示与供应商错误体进日志

`CharApp/minimall/server.py:390` 的 `build_service(writer=logger.info)` → `CharAgent/client/app.py:260-266` 的 `_retry_notice` 写 `attempt.reason` → `CharAgent/retry/executor.py:154` 拼 `f"{type(exc).__name__}: {exc}"` → 异常文本来自 `CharAgent/model/client_httpx.py:186-190` 的 `extract_error_message(response.text)`。

也就是**供应商的错误响应正文**会顺着这条链进日志。**修法见第三节** —— 这一条正好是"业务在装配处包一层"的样板。

## 二、框架侧：`Redactor` 协议 + 通用规则

**判据照 PRD §4.12**：换成 code agent 还能用吗？—— 通用规则能（手机号 / 邮箱 / 身份证 / 银行卡 / 密码类字段名是**跨业务**的），所以放框架。

**形状**（#26 的原话是"按字段类型脱敏而非正则碰运气"，所以**两种接口要分开**）：

```python
class Redactor(Protocol):
    def redact_text(self, text: str) -> str: ...                      # 自由文本：规则兜底（承认是最后一道）
    def redact_fields(self, data: Mapping[str, Any]) -> dict[str, Any]: ...   # 结构化：按字段名/类型打码（主手段）
```

- **`redact_fields` 是主手段**：业务声明字段路径（如 `customer.phone` → 手机号规则、`*.payment_password` → 整段抹掉），框架按声明打码。**这是"知道它是什么，就按它是什么打"**。
- **`redact_text` 是最后一道**：规则型，承认它在碰运气。**它兜不住的不假装兜得住** —— docstring 要写明这一条，与 ADR-0003「这条保证的范围」那段同一种写法。

**落点**：`CharAgent/` 下新包（`redact/` 或与日志同在一处，实施时定）。**注意一条已记录的地雷**：`PLAN.md` §3.1 写明 `pyproject.toml` 的 `packages.find` 必须显式设 `namespaces = false` —— 加新包**要确认它被收进 wheel**，否则装到别处会 `ImportError`。

**框架侧"要不要顺手统一 logger 命名"**：今天 5 个模块各自硬编码 `charagent.<子包>`（`client` / `prompt` / `db` / `agent` / `server`），**没有集中常量**，而三条测试按**字符串名字**抓（`test_db_recorder.py:346` / `test_prompt_ref.py:39,94` / `test_server_runs.py:149`）。**本片不动它** —— 与脱敏无关，动它会连带动测试，属于"顺便优化"，不做。

## 三、业务侧：注册字段路径，并在装配处包一层

- `CharApp/minimall/` 声明自己的字段路径（买家手机号、地址、余额、支付密码）
- 第 ③ 个泄漏点就是样板：`build_service(writer=...)` 那里把出口包一层 `redactor.redact_text` —— **一处装配改动**，不碰框架

**`CharApp/minimall/redaction.py` 不要动，也不要合并**：它是 **ADR-0003 的事件脱敏**（把 `tool_call` / `tool_result` 的载荷整条换成一句 `label`），本片是**日志脱敏**（按字段打码）。**两件事，两个用**：一个是"整条别出去"，一个是"打码后再出去"。它的 docstring（`:7-11`）已经把范围写死了（只管工具事件），**保持那句话有效**。

## 交付物

| # | 内容 |
|---|------|
| 1 | CharApp：关掉 uvicorn access log（`server.py:347-349`），并在注释里写明**为什么**（URL 可能带用户数据） |
| 2 | Django：`_detail` 的正文不再进日志（`:832` / `:970` / `:1114` 三处），改成记状态码 + `_refusal_code` |
| 3 | 框架：`Redactor` 协议 + 通用规则实现（`redact_text` / `redact_fields`） |
| 4 | 业务：在装配处包一层（`CharApp/minimall/server.py:390` 的 `writer`） |
| 5 | 用例：`redact_text` 对手机号 / 邮箱 / 身份证 / 银行卡各一条；`redact_fields` 按路径打码；**规则兜不住的自由文本如实不改**（钉住"不假装"） |

## 验收

- [ ] **构造一条含手机号的日志，落盘后搜不到原文**（PLAN 定的那条）
- [ ] 搜索一次（用一个像订单号的词），CharApp 侧**任何日志里都搜不到那个词**
- [ ] 上游返回 403 时，Django 日志里有状态码与错误码、**没有**上游正文
- [ ] 模型重试发生时，日志里的重试提示过了 `redact_text`
- [ ] `CharApp/minimall/redaction.py` 的行为**一字不改**（ADR-0003 照旧）
- [ ] 现有 1052 条用例（框架）+ CharApp / minimall 侧用例全绿

## 备注

- **不加"演示开关"**：ADR-0003 已经否过一次（"一个「演示时把敏感数据打开」的环境变量是安全反模式"），本片同一条纪律。
- **不做正则大杂烩**：规则只上"确定知道形状"的（手机号 11 位、邮箱、身份证、银行卡），**不猜**订单号（本项目订单号的形状是实现细节，猜错了会误伤）。订单号这一类靠**不打它**（第 ①②条）而不是靠规则。
- **与 issue 28 的关系**：本片管"别泄漏"，28 管"能查得到"。两者都动日志/观测面但目标相反，**不要合成一片**。
- 与将来 #28（数据合规删除权）的关系：本片只管**日志**；库里存着的原文是 ADR-0016 明确接受的，那笔账记在那里。
