# 01 — 骨架 + 最窄切片（一条链路端到端打通）

**What to build:** CharService 子项目骨架（`api/` `channels/` `identity/` `gateway/` `tools/` `frontend/` 目录 + 配置 + 测试骨架；**无 `worker/`——退款 worker 归 minimall**）；模拟渠道（`POST /sessions` 带 `user_id`，模拟 App 已鉴权）；身份服务最小版（`POST /token`：服务凭证校验 → 签发 RS256 委托 token，含 `sub`/`act`/`aud`/`tenant`/`exp`）；内部网关最小版（验签 + 转发，暂无限流与审计）；minimall 新增 `/api/minimall/internal/support/orders/<order_no>/support-view`（订单 + 物流 + 售后资格，按任务粒度一次返回）；单个工具 `get_order_detail`（薄封装：参数校验 + 调网关 + 格式化）；主服务组装 CharAgent 的 runtime router。验收即这一条链路真实跑通。

**工具怎么拿到身份**（ADR-0010）：签名里**没有** `user_id`，用一个 `Injected` 参数声明需求，由框架在运行时从 run 上下文填充——

```python
@tool
def get_order_detail(
    order_no: Annotated[str, Field(pattern=r"^\d{14}$")],
    ctx: Annotated[ToolContext, Injected()],   # ← 不进 schema，模型看不到
) -> str | Suspension: ...
```

**凭据续期**：客服会话跨越 15 分钟是常态，token 不能「建会话时换一次就锁死」。主服务持一个**凭据提供者**，在每次 run 前构造 `ToolContext` 时检查剩余有效期，不足就向身份服务重换。

**Blocked by:** **CharAgent P1-1**（runtime router 工厂）、**CharAgent P1-15**（`Injected` / `ToolContext` 通道）

**Status:** ready-for-agent

- [ ] CharService 骨架落地，主服务 :10070 可启动（**无 `worker/` 目录**）
- [ ] 身份服务 :10072 签发委托 token，RS256（私钥仅在 identity，网关只持公钥）
- [ ] 网关 :10071 验签 + 转发，签名错误 / 过期 / 受众不符一律拒绝
- [ ] minimall `internal/support/orders/<no>/support-view` 可用（订单 + 物流 + 售后资格一次返回）
- [ ] `get_order_detail` 用 `Injected` 声明 `ctx`；**验证模型的 schema 里没有 `ctx`**
- [ ] **凭据提供者**：run 前检查有效期、不足则重换；用例 —— token 到期后（模拟）下一次工具调用仍成功
- [ ] 端到端：渠道给 `user_id` → agent 调工具 → 网关 → minimall → 返回真实订单数据
- [ ] 越权用例：持用户 A 的 token 查用户 B 的订单 → **网关拒绝**（不靠业务系统兜底）
- [ ] 伪造用例：agent 侧自签 token → 验签失败
- [ ] 工具层代码里**没有** minimall 地址与 MySQL 凭据（代码级检查 —— 这是「限流与审计不可绕过」的前提）
