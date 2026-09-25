# 29 · 日志脱敏：先堵三个实证泄漏点，再给通用规则

**Status:** done

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

- [x] **构造一条含手机号的日志，落盘后搜不到原文**（PLAN 定的那条）——
      `CharApp/tests/test_log_redaction.py::test_a_phone_number_never_reaches_the_log_file`：
      真写文件再读回来，而那一行由框架的 `_retry_notice` 拼（不是测试里抄一份格式）
- [x] 搜索一次（用一个像订单号的词），CharApp 侧**任何日志里都搜不到那个词** ——
      真机：服务起在 1008，打 `GET /conversations?q=202609250008880000109999` → HTTP 200，
      日志里那个词 **0 次**、访问日志行 **0 行**（关掉 access log 之后一行都不再产生）
- [x] 上游返回 403 时，Django 日志里有状态码与错误码、**没有**上游正文 ——
      真机：假上游回 403（正文里带订单号 / 姓名 / 手机号），日志是
      `买家 10: 客服服务拒绝了这次列会话: HTTP 403 (forbidden)`，正文那三样一个都搜不到
- [x] 模型重试发生时，日志里的重试提示过了 `redact_text` —— 真机：假上游回 503，
      日志是 `[retry] 第 1 次尝试失败, 0.5s 后重试: ... 手机号 138****0003 ...`
- [x] `CharApp/minimall/redaction.py` 的行为**一字不改**（ADR-0003 照旧）—— 该文件没进本次 diff
- [x] 现有用例全绿 —— 实测：**CharAgent 1236 passed**（105 deselected，含 pg/redis/integration）、
      **CharApp 207 passed**、Django **`app.minimall.tests.test_bff` 103 passed**、
      `ruff check` + `format --check` 全过（683 个文件）

## 备注

- **不加"演示开关"**：ADR-0003 已经否过一次（"一个「演示时把敏感数据打开」的环境变量是安全反模式"），本片同一条纪律。
- **不做正则大杂烩**：规则只上"确定知道形状"的（手机号 11 位、邮箱、身份证、银行卡），**不猜**订单号（本项目订单号的形状是实现细节，猜错了会误伤）。订单号这一类靠**不打它**（第 ①②条）而不是靠规则。
- **与 issue 28 的关系**：本片管"别泄漏"，28 管"能查得到"。两者都动日志/观测面但目标相反，**不要合成一片**。
- 与将来 #28（数据合规删除权）的关系：本片只管**日志**；库里存着的原文是 ADR-0016 明确接受的，那笔账记在那里。

## 实现记录（2026-09-25）

**框架侧新包 `CharAgent/redact/`**（顺手把门面补齐：根 `__init__.py` 与 `test_root_facade.py` 的包名单九层 → 十层）

| 文件 | 一句话 |
|------|--------|
| `protocol.py` | `Redactor` 协议：`redact_text`（自由文本，最后一道）+ `redact_fields`（按字段路径，主手段） |
| `rules.py` | 四条规则（手机号 / 邮箱 / 身份证 / 银行卡）+ `mask_text`；数字边界用 `(?<!\d)` 而**不是** `\b`（中文在 `re` 里也算 `\w`，用 `\b` 的规则在中文日志里静默失效 —— 有专门一条用例钉它） |
| `redactor.py` | `RuleRedactor`：路径匹配（`*` 一层 / `**` 任意层，与 glob 同义）+ `WIPE` 抹除；**返回新树、原件不动**；声明写错在**构造期**报 |
| `utils/errors.py` | `RedactError` / `RedactConfigError` |

**业务侧**（新模块 `CharApp/minimall/log_redaction.py`）：`LOG_FIELDS` 名单（字段名照 `app/minimall/serializers_agent.py` 抄，写法用 `**.名字`）、`build_redactor()`、`redacting_writer()`；`server.py` 新增 `log_writer()`（`main` 把它交给 `build_service`）与 `uvicorn_config()`（`access_log=False` 单独一处、可被用例钉住）。

**Django 侧**（`app/minimall/views_bff.py`）：三处拒绝日志改成**状态码 + 错误码**；`_detail` **删除**（它存在的理由就是这条泄漏，删掉之后没有别的调用方）；`_DETAIL_LIMIT` 改名 `_FRAME_LOG_LIMIT`（只剩「帧解析不了」那一条路在用）。

**顺手改掉的第 4 处（评审提出，改法见下）**：`_with_user_copy` 那条「帧解析不了就原样转发」的分支，原来把**原始帧字节**截 200 字符打进日志 —— 那个截断常量（`_DETAIL_LIMIT`，我一开始只是把它改名成 `_FRAME_LOG_LIMIT`）在本片之前正是「截一段上游内容进日志」的同一件工具，只是截的东西从「响应正文」换成了「事件帧」。核实三点：

- 那一帧**本来就会原样转发给浏览器**（同一买家看得见），所以记录它不等于新披露；
- 但它的 message **不受任何固定表约束** —— 终端 error 帧里 `run_failed` 那条直接带异常文本，真机上就带着模型上游回的正文。我原先在记录里写「取 `TERMINAL_ERROR_TEXT` 固定表、不含上游正文」，那是把**解析成功**那条路的结论套到了**解析失败**这条路上，评审指出后改正（`_with_user_copy` 的 docstring 也一并改准了）；
- 于是这一条改成记「哪条帧、多少字节、解析为什么失败」，**不记原文**：要查那一帧，看页面收到的那一段就行。`_DETAIL_LIMIT` 这个常量随之**彻底消失** —— 本片之后，Django 侧再没有一处「把上游内容截一段进日志」。

**真机那一趟**（charagent 服务起在 1008，模型指到一个只会回 503 的假上游、正文里塞着手机号与订单号）：

```
① GET /conversations?q=202609250008880000109999      → HTTP 200，日志里该词 0 次、访问日志 0 行
③ POST /runs {"message": "我的余额还有多少"}
   → 日志: [retry] 第 1 次尝试失败, 0.5s 后重试: ... 手机号 138****0003 ...   ← 过了出口
   → 日志: 运行 ... 异常终止: ModelStatusError + traceback: ... 手机号 13800000003 ...   ← **没**过出口（见下）
```

**已知未堵的一条（真机同一趟里发现的，留给「日志结构化」那一片 —— `DESIGN.md` 的 #38，注意别与本仓 issue 38「L3b 收口」混了；**那一题还没切 ticket**）**：框架自己打的**异常栈**不经过业务递给它的 `writer` —— 同一次运行里，重试提示那一行手机号是 `138****0003`，而框架 `charagent.client` 打的「运行异常终止 + traceback」里**同一段供应商正文还是原文**。根因不在本片能包住的那个出口上：框架目前**没有统一的日志出口**（五个模块各自 `logging.getLogger("charagent.<子包>")`），而本片按工单明确「不动那套 logger 命名」。落点写在 ADR-0019 与 `CharAgent/docs/DESIGN.md` 的 #26 落地行里：**#38 一起解决**。

**先前就红的 4 条（本次顺手改掉了，与日志脱敏无关）**：`app.minimall.tests.test_bff.AgentPageTest` 里 4 条**页面源码断言**在 HEAD 上就红 —— 页面那段 JS 在 ticket 25 改过、断言没跟上。用户拍板「改写成钉行为的粒度」（不是删用例，因为每条用例里还带着 2–4 条**仍然有效**的断言），于是：

| 用例 | 改掉的断言 | 为什么这么改 |
|------|-----------|-------------|
| `test_the_page_restores_the_conversation_on_load` | `"loadHistory();"` → 用**原文**匹配顶层的 `loadHistory(` | 那一句启动时拉历史还在，只是多了一个参数；「在不在函数里」只有看原文的行首才知道（新加 `raw_page_source()` 帮手） |
| 同上 | `"renderHistory(payload.messages \|\| [])"` → 拆成 `renderHistory(messages)` + `payload.messages \|\| []` | 读回来的东西照样进同一套渲染，只是那一句被拆成两行了 |
| `test_a_new_conversation_only_swaps_the_id_and_clears_the_room` | `"switchConversation(newConversationId())"` → 正则到 `switchConversation(newConversationId()` 为止 | 行为是「新建走同一个切换函数」，不钉它带几个参数 |
| `test_renaming_swaps_in_a_real_input` | `"displayTitle(input.value \|\| '')"` → `displayTitle(title)` | 这条**不是**改写法：取消改名现在显示的是**原标题**，而旧写法是当时就修掉的假话（按 Escape 后显示你刚敲的名字、库里一个字没改） |
| `test_the_sidebar_has_a_debounced_search_box` | 整条改名为 `test_the_sidebar_search_fires_on_submit_and_keeps_the_term_out_of_the_url`；防抖三条换成「form 的 submit 接上了搜索」 | ticket 25 把「输入即搜 + 300ms 防抖」改成了「按按钮/回车才搜」（页面上那句注释写着），断言跟着改；**保留**「词走请求体、不进地址栏」那两条（与 issue 29 同源） |

顺着定下的口径（写进上面几条的注释里）：页面源码断言只钉**行为的关键点**（哪个函数被调用、请求体里带什么、哪句文案在不在），**不钉整条语句的字符** —— 前端换写法不该红，行为被删才红。

**真机跑留下的痕迹**：`charagent_threads` / `charagent_runs` 里多过一段买家 10 的会话（`X-Conversation-Id: logredact`，那次运行失败、run_id `8c3062c570044361809ac5a16cb3b7be`）—— **用户已于 2026-09-25 清掉**；1010 端口上那个假的商城进程（我起用来验第 ② 条的）**也已由用户停掉**。验证脚本留在 `%TEMP%/logredact/`（假的模型上游 / 假的商城 / 请求体），归用户那个定期清理的临时目录管。

## CodeReview（2026-09-25，两轴并行）

**规范轴**

| # | 发现 | 处置 |
|---|------|------|
| 1 | `test_log_redaction.py` 的悬挂缩进不合 `ruff format` | 已 `ruff format`（仓库 683 个文件全过） |
| 2 | `test_server.py` 一条用例钉三件事（access_log / log_level / host+port），名字只覆盖第一条 | 改名为 `test_the_uvicorn_settings_differ_from_the_defaults_only_in_the_access_log`，docstring 写明后两句是「没顺手带偏别的」的守卫（与既有 `test_the_server_config_comes_from_the_env` 同一个粒度） |
| 3 | Speculative Generality：字段路径那一半（`_parse`/`_apply`/`WIPE`/`WIPED`/名单）生产零调用，为 `DESIGN.md` #38 预支约 80 行 | **保留** —— 工单交付物 3 点名要它，`DESIGN.md` #26 也把「按字段类型打码」定为主手段；「还没有调用方」这条已如实写进模块 docstring 与 ADR-0019 |
| 4 | Duplicated Code / Shotgun Surgery：三处拒绝日志同一形状（≥3 该抽）；`CharApp/minimall/client.py` 还留着同款的 `_detail`/`_DETAIL_LIMIT` | 前半采纳：抽成 `_log_refusal(who, action, response, *, level)`，**一处定「拒绝时记什么」**并返回码，三条路都走它；后半核实后**不改**：那个文件里**一个 logger 都没有**，`_detail` 只喂异常消息（进模型与 traceback），不是日志 —— 它正是「框架异常栈那条残路」的上游，落点记在 #38 |
| 5 | Repeated Switches：`_apply` 里 `*`/`**` 在 Mapping 与 list 各写一遍 | **不改**（判断题）：四条分支各自要处理的东西不同（列表没有键名、`**` 多一步「先当终点试一次」），合并要引入两个只为省分支而存在的 helper，得不偿失；行为有用例覆盖 |
| 6 | Primitive Obsession：声明用哨兵串 `"<wipe>"`；`WIPE` 与 `WIPED` 一字之差 | **不改**：写反的后果被构造期拦住（声明里写 `WIPED` → 「不认识的规则名」当场报），不需要再加一层类型 |
| 7 | Middle Man：`log_writer()` 名字比实际宽（本进程别的日志不过它） | 采纳其**实质**：docstring 开头改成「**交给框架的那个**日志出口」，并明写它盖不到框架自己打的异常栈（指向 `DESIGN.md` #38）；名字保留（它就是「写日志的那个 writer」） |
| 8 | 测试名自称「商城真在用」，断言只比对 `LOG_FIELDS` 自己 | 采纳：改成与 `conftest` 的样本载荷（PROFILE / ADDRESSES）对账 —— 两份独立抄件互相印证，名字也改成 `..._the_sample_payloads_carry` |

**规格轴**

| # | 发现 | 处置 |
|---|------|------|
| 1 | ②「不打上游正文」只落在点名的三处；`_with_user_copy` 还在打帧原文 | 成立，已改（见上面「顺手改掉的第 4 处」） |
| 2 | 实现记录里「那一帧不含上游正文」是循环论证 | 成立，已改正（同上） |
| 3 | `redactor.py`：`**` 结尾的路径（`{"a.**": WIPE}`）构造期放行，打码时抛 `TypeError`（评审实测复现） | 成立，已改成**最后一段不许是通配符**（构造期报，附一条用例；`{"**": WIPE}` 这类也在同一条规则下被拦） |
| 4 | 超范围：`_DETAIL_LIMIT` 改名、CONTEXT.md 两词条、DESIGN.md 一行（交付物表没列） | 改名那条随第 4 处一起消失了（常量删掉）；CONTEXT / DESIGN 保留 —— 仓库惯例是「出现新术语与落地结论就同步」，且 ADR-0019 要引它们 |
| 5 | 禁改项全线守住：`CharApp/minimall/redaction.py` 零 diff、框架五模块 logger 没动、无演示开关、订单号没写规则 | 无需处置 |
