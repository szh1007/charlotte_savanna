# minimall Redis 缓存设计文档

> 2026-07-29 | 本文档记录项目 Redis 缓存的设计、风险修复和使用方式。

---

## 一、架构概览

```
  Request
     │
     ▼
┌────────────┐  miss   ┌─────────┐  miss   ┌────┐
│  L2 Redis  │ ──────→ │  DB     │ ──────→ │ DB │
│  (cache)   │ ←────── │ (loader)│ ←────── │    │
└────────────┘  回填    └─────────┘  查询   └────┘
     │ hit
     ▼
  Response
```

- 仅 L2 缓存（Redis），无本地 L1 缓存
- Redis 位置：`redis://127.0.0.1:6379/1`
- 客户端库：`django-redis`
- 后端：`django.core.cache.backends.redis.RedisCache`

---

## 二、缓存项设计

| 缓存 Key | 内容 | TTL | 失效策略 |
|----------|------|-----|---------|
| `minimall:category_tree` | 分类树 JSON | 1h（兜底上限） | 分类变更时主动删除 |
| `minimall:product_list:{hash}` | 筛选后的商品列表 | 5min ±20% | 商品/分类变更时批量删除所有列表缓存 |
| `minimall:product_detail:{slug}` | 单个商品详情 | 10min ±20% | 商品/图片变更时删除对应 key |
| `minimall:featured_products` | 首页推荐商品 | 5min ±20% | 商品变更时删除 |
| `minimall:product_list:index` | Redis Set — 所有列表 key 的索引 | — | 用于精准删除，避免 KEYS/SCAN |

---

## 三、已修复的缓存风险

### 1. 缓存击穿（Breakdown）✓

**问题**：热门 key 过期瞬间，并发请求同时穿透到 DB。

**修复**：
- Redis `SETNX` 分布式锁，仅一个请求执行 loader 回填
- 其他请求通过 Redis Pub/Sub 阻塞等待（最多 3 秒）
- 超时兜底直接查 DB

**实现**：`cache.py:_lock_and_load()`

---

### 2. 缓存穿透（Penetration）✓

**问题**：恶意请求不存在的 slug，每次穿透到 DB 查 `get_object_or_404`。

**修复**：
- 商品详情 loader 返回 `None` 时，缓存空值标记 `{key}:null`（TTL=60s）
- 后续请求命中空值标记，直接返回 None，不查 DB

**实现**：`cache.py:_get_or_load(allow_null=True)`

---

### 3. 缓存雪崩（Avalanche）✓

**问题**：大量 key 同时过期，流量瞬间打到 DB。

**修复**：
- 所有 TTL 加 `±20%` 随机偏移（`_jitter()`）
- 不同时刻写入的 key 不会同时过期

---

### 4. 自旋锁占用 Worker ✓

**问题**：原实现用 `time.sleep(0.1)` 轮询 30 次等待锁，阻塞线程。

**修复**：
- 改用 Redis Pub/Sub 阻塞等待
- 获锁者 `publish` 通知，等待者 `subscribe` 接收
- 3 秒超时兜底

---

### 5. `delete_pattern` 全量扫描 ✓

**问题**：`cache.delete_pattern("product_list:*")` 底层用 `KEYS` 或 `SCAN`，key 多时阻塞 Redis。

**修复**：
- 维护 `product_list:index` Set，写入缓存时 `SADD`
- 失效时 `SMEMBERS` + `Pipeline` 精准批量删除
- 索引丢失时兜底 `delete_pattern`

---

### 6. 缓存一致性（Cache-Aside）✓

**问题**：先删缓存再写 DB，写失败则缓存已被清空。

**修复**：
- Signal 用 `post_save/delete`
- `transaction.on_commit()` 确保 DB 事务提交后删缓存
- 事务回滚 → 缓存不受影响

---

### 7. Redis 宕机（熔断器）✓

**问题**：Redis 完全不可用时，每个缓存调用抛异常 → 500。

**修复**：
- 所有缓存操作包装 `_safe_cache()`，失败返回 `None`/`False` 不抛异常
- 连续 3 次失败 → 熔断（30s） → 所有请求直接走 DB
- 30 秒后自动尝试恢复

**监控**：
- 每次失败打 ERROR 日志，含操作名和异常信息
- 熔断/恢复打 WARNING/INFO 日志
- Admin 后台左上角红/绿圆点实时显示状态

---

### 8. 冷启动穿透 ✓

**问题**：服务重启后缓存全空，首批请求全部穿透到 DB。

**修复**：
- `AppConfig.ready()` 中调用 `warmup_cache()`
- 预热分类树 + 默认商品列表（首页 TOP 20）
- `_warmed_up` 标志防重复执行

---

### 9. 锁超时固定 ✓

**问题**：`LOCK_TIMEOUT = 30s` 偏短，慢查询场景锁提前释放导致重复查询。

**修复**：增至 60s；Pub/Sub 等待同时每 0.5s 轮询 `cache.get()` 作为消息丢失兜底。

---

### 10. Pub/Sub 消息丢失 ✓

**问题**：Redis Pub/Sub 不持久化，通知消息可能丢失。

**修复**：Pub/Sub 阻塞等待同时每 0.5s 轮询检查缓存是否已被填充，两者并行确保不丢通知。

---

### 11. `product_list:index` Set 泄漏 ✓

**问题**：索引 Set 无 TTL，理论上可无限增长。

**修复**：SADD 时追加 `EXPIRE 86400`（24h），无写入时自动清理；商品变更时整个 Set 重建。

---

### 12. 分类树脏数据窗口 ✓

**问题**：Signal 漏触发（如直接改库）导致分类树缓存永不更新。

**修复**：`CATEGORY_TREE_MAX_TTL = 3600` 兜底，1h 后强制过期刷新。

---

### 13. 预热失败无提示 ✓

**问题**：启动时 Redis 不可用，预热静默失败。

**修复**：`warmup_cache()` 使用 `try/except` + `logger.warning`，失败不阻塞启动，后续正常请求走穿透。

---

## 四、相比企业级项目欠缺的设计

| 特性 | 说明 | 当前项目适用性 |
|------|------|:---:|
| **Metrics 指标上报** | 命中率/穿透率/锁等待耗时 → Prometheus + Grafana | 单机项目无监控基建 |
| **本地 L1 缓存** | 进程内存 + Redis L2，减少网络开销 | 无高并发需求 |
| **Hot Key 检测** | 自动发现热点 key，加本地缓存或拆分 | 单 key 数据量小 |
| **Big Key 检测** | 防止 value 过大阻塞 Redis 单线程 | 列表分页后 value 可控 |
| **Binlog 订阅一致性** | Canal/Debezium 监听 DB 变更主动失效 | bulk_create 场景极少 |
| **多级降级策略** | Redis 慢查询 → 半熔断 → 全熔断 → DB | 慢查询场景极少 |
| **序列化优化** | msgpack/protobuf 替代 pickle，更快更小 | 数据量小，无意义 |
| **读写分离** | Redis Sentinel/Cluster，主写从读 | 单节点够用 |
| **血统追踪 (Lineage)** | 每个缓存 entry 记录 DB row version，避免脏读 | 需要 DB 改造 |
| **租约机制** | 缓存过期不立即删除，先查 DB 比对版本号 | 需要 DB 改造 |
| **预热脚本** | 独立的预热任务，从 DB 批量加载到 Redis | 启动时自动预热已够用 |
| **缓存编排平台** | 可视化管理缓存配置/失效/回滚 | SaaS 平台才有 |

---

## 五、使用示例

```python
# 读取分类树（无过期 + 1h 兜底 + 变更失效）
data = get_cached_category_tree()

# 读取商品列表（5min TTL + 击穿锁 + 索引管理）
data = get_cached_product_list(params_hash, loader)

# 读取商品详情（10min TTL + 空值穿透保护）
data = get_cached_product_detail(slug, loader)

# 商品变更后失效（signal 自动调用）
invalidate_product_cache(product)

# 启动预热（apps.py 自动调用）
warmup_cache()
```
