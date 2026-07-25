# 19 — Tip 打赏

**What to build:** 打赏同步事务（余额校验→扣减→入账→流水）、余额不足弹窗。

**Blocked by:** 05 — JWT 认证, 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `Tip` 模型：id, from_user_id, to_user_id, post_id, amount, created_at
- [ ] `POST /api/posts/{id}/tip`：接收 amount(>0) → 校验余额（`from_user.credits >= amount`）→ 同一事务内：from_user.credits -= amount → to_user.credits += amount → 写入 Tip 流水 → 更新 post.tip_count+1 + post.tip_total+=amount → 提交
- [ ] 余额不足时返回 `{code:0, message:"Credits不足，是否前往充值？"}`
- [ ] 不能给自己打赏
- [ ] `GET /api/posts/{id}/tip/records`：某帖子的打赏记录列表
