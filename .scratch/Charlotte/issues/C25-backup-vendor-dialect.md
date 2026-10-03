# C25 · 备份端点的方言：带 tools 必须显式关思考

**Status:** done

**Type:** fix

**上游:** `CharApp/docs/adr/0025`（决定 5 的边界：备份是另一家）；2026-10-05 真机 failover 实测发现

## 现象（真机第一次触发 failover 时）

闸跳了、也切到备份了、请求真的发过去了 —— 然后被**备份端点**拒掉：

```
ModelStatusError: 模型 API 返回 HTTP 400: Function tools with reasoning_effort are not
supported for gpt-6-luna in /v1/chat/completions. To use function tools, use
/v1/responses or set reasoning_effort to 'none'.
```

也就是说：**failover 机制本身跑通了**（跳闸 → 切换 → 打到备份），但备份这条路当时是**死的**。

## 定因：三种形状的对照实验（直接打那个端点）

| 请求形状 | 结果 |
|---|---|
| 带 `tools`，**不带** `reasoning_effort` | **400**（它的默认 = 带 reasoning） |
| 带 `tools` + `reasoning_effort="none"` | **200 ✓**（正常回 `tool_calls`） |
| 带 `tools` + `reasoning_effort="high"` | 400 |

结论：**不是我们多发了参数，而是这一家要求「必须显式关掉思考」才肯带 tools** —— 我们
对备份模型一个 effort 都不传，正中它的默认。

## 决定

**在装配处给备份适配器一个实例默认 `reasoning_effort="none"`**
（`client/app.py` 的 `_FALLBACK_REASONING_EFFORT` + 直接造 `HttpXChatModel`）。

- 调用方不显式传 effort 时（现在两个入口都不传），每次请求都会带上 `"none"` ✓
- **为什么不动 `FailoverChatModel`**：它的契约是「不吞不改任何参数」（#68）——
  让通用包装去改参数，等于把**某一家供应商的脾气**焊进通用层。方言归装配。
- **为什么直接造适配器而不是走 `chat_model_from_env`**：那个工厂读的是主模型那套
  env（`DEEPSEEK_*`），而这里三个值都是显式的、还多一个方言默认 —— 为这一处给工厂
  加参数不划算（剥前缀那一步照抄它）。

## 验收

- [x] 真机 failover 跑通：闸跳 → 切到 `gpt-6-luna` → **备份正常发起工具调用** →
      模型收尾（`[failover] ... 本次调用改走 gpt-6-luna` 与 `[tool_call] ...` 都在终端上）
- [x] 那一趟的账目正确：`model=gpt-6-luna`、`usage_by_model=[{gpt-6-luna, in=12403,
      out=41, hit=6164}]`（这一家不报未命中，所以键缺省）、
      金额 `0.007060` = 6239x¥1/M（**推自 input**）+ 6164x¥0.1/M + 41x¥5/M —— 按
      **备份自己的价目表**算的
- [x] 单测：`test_client_app.py` 48 passed（备份工厂与两层包装的接线不变）

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/client/app.py` | `_FALLBACK_REASONING_EFFORT = "none"` + `fallback_model_from_env` 直接造 `HttpXChatModel`（带那个实例默认）；docstring 记下方言与「为什么不放包装里」 |
| `.env.example` | 备份那一段加一句：这一家带 tools 必须显式关思考（代码已按它配好；换家要重看这一条） |

## 实施记录

- **发现的路径**：C23/C24 落地后按计划做「真机触发一次 failover」（把主模型 base_url
  指到连不上的本地端口 —— 连接失败是瞬态，会被计账、连错 3 次跳闸）。第一次跑就撞上
  这个 400，是**假模型用例永远抓不到**的那类问题。
- **对照实验**：同一台机器上「不投毒走主模型」同样能复现工具失败 —— 那是商城内部端点
  本身 502（`CHARAPP_BASE_URL/` 根路径也 502，Django 服务没起或前面有代理），**与本次
  改动无关**；写在这里免得下次把它当成 failover 的锅。
- **顺带的环境事实**（不是问题，是排查经验）：这台机器上有 HTTP 代理在拦，连
  `http://127.0.0.1:9/` 都会回 **502 空响应体**（而不是连接拒绝）—— 造「瞬态失败」时
  正好合用（502 是瞬态 ✓），但下次别指望「连不上 = 连接被拒」那条经验。
- **已知边界（留给以后）**：如果业务以后给**所有调用**显式传 `reasoning_effort`（现在
  两个入口都不传），备份会再次被拒（调用级参数优先于实例默认）。到那时要么按「哪一家
  支持什么」把参数能力下沉到适配器，要么换一家备份端点 —— 这条写进票里，不预做。
