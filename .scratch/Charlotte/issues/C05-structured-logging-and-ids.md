# C05 · #38 结构化日志 + id 贯穿（含框架异常栈过脱敏出口）

**Status:** todo

**Type:** feature

**Blocked by:** —

**上游:** `CharAgent/docs/DESIGN.md` §4 ⑥ 的 #38（「本册只落了 #41 的一小块」段）；`CharApp/docs/adr/0019`（日志先脱敏再写）；`.scratch/Charlotte/PLAN.md` §4 组 B

## 现状（2026-09-30 读码核实）

| 事实 | 证据 |
|---|---|
| 框架有 **6 个各自为政的 logger** | `agent/compaction.py:95` · `client/session.py:110` · `db/recorder.py:141` · `prompt/ref.py:47` · `server/app.py:228` · `server/runs.py:46` —— 全部 `logging.getLogger("charagent.<子包>")` |
| **仓里没有统一出口** | 没有任何地方 `addHandler` / `setFormatter` —— 日志长什么样由**部署方**决定 |
| **框架自己打的异常栈不过脱敏出口** | `Redactor` 协议（`redact/protocol.py`）已经在了，业务侧也注册了字段名单（`CharApp/minimall/log_redaction.py`），但框架的 logger 从不经过它 |
| 访问日志已整个关掉 | `adr/0019`（搜索词会走查询串）—— **这一条要保持，别在本次改回** |

**这一片要闭合的是这条链**：脱敏（#26，已落）→ **结构化日志（#38，本次）** → trace 回放（#41，`client/trace.py` 已落）。

---

## 一、统一出口

框架提供**一个**配置入口，业务在启动时调一次：

- 位置：新建 `CharAgent/logging/`（**不要塞进 `redact/`** —— 脱敏是出口上的一道工序，不是出口本身；两者分开才讲得清「先脱敏后格式化」）
- 形状：`configure_logging(*, level, redactor, json=True, stream=None)` + 一个 `get_logger(name)` 工厂
- **零新依赖**：JSON 格式化自己写（~30 行），**不引入 structlog / loguru** —— 「零框架依赖」是这个项目立身的那句话，`pyproject.toml` 那 8 项就是它的证据，不能为了日志破例

## 二、脱敏在格式化之前

```python
class RedactFilter(logging.Filter):        # 顺序是关键
    def filter(self, record):              # 1. record.msg / record.args → redact_text
        ...                                # 2. record.__dict__ 里的结构字段 → redact_fields
        ...                                # 3. exc_info 的 traceback 文本 → redact_text
```

**三个落点都要过**，其中第三个是本次的**核心**（DESIGN #38 点名「框架自己打的异常栈还没过出口」）：

- `record.msg` 与 `record.args`（拼好的一句话）
- 结构化字段（`extra=` 传进来的 dict）
- **`exc_info`**：`logging.Formatter.formatException` 吐出来的那段文本，要**先格式化再打码再拼回去** —— 直接对 `record.exc_text` 动手

## 三、三个 id 贯穿

| id | 从哪来 | 挂到哪 |
|---|---|---|
| `thread_id` | `run_context` / checkpoint 分区键 | 每次写入自动带上 |
| `run_id` | `AgentLoop` 的 loop_id / recorder 的 run | 同上 |
| `request_id` | **server 层**从 HTTP 头取（`X-Request-Id`，没有就生成） | 同上 |

**实现用 `contextvars`，不能用 `threading.local`** —— 框架是 asyncio 的，`to_thread` 里的同步代码也要能读到（`contextvars.copy_context` 会带过去）。**这一条要在票里写死，它是本片最容易做错的地方。**

对照：`project/rag_text2sql` 里 `main.py:19` 把 `request_id` **写死成 `"charlotte"`**，于是 HTTP 链路上所有请求是同一个 id，"链路追踪"是空的 —— 那正是本片要避免的形态。

## 四、验收

- [ ] 构造一条**含手机号的框架异常**（不是业务异常），落盘日志里搜不到原文，但能搜到打码后的形状
- [ ] 同一次运行的所有日志行都带 `thread_id` / `run_id` / `request_id` 三个字段
- [ ] **HTTP 路径上，两个并发请求的 `request_id` 不相同**（这一条直接钉住"别学 rag_text2sql 写死"）
- [ ] 日志是 JSON，一事件一行，可被 `jq` 直接消费
- [ ] `pyproject.toml` 的依赖**仍然是 8 项**（没有为日志引入新依赖）
- [ ] 访问日志仍然关着（`adr/0019` 不被回退）
- [ ] 现有 1502 个用例全绿

## 开工前要定的

- 业务侧 `CharApp/minimall/log_redaction.py` 的字段名单要不要扩（本次改动会让**框架的**日志也过这个出口，可能暴露出此前没被覆盖的字段）
- 是否顺手把 `client/trace.py` 的 `--view` 输出也改成 JSON（可选，不做也行）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
