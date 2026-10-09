# Mission: 中间件开发底座（数据库 / 缓存 / 消息队列）

> 面试导向。目标不是「学过」，而是**被追问三层还能答下去，并用自己的项目举例**。

## Why

求职期的后端面试，中间件是必考区。目前的状态：数据库（MySQL / PostgreSQL）有真实的项目接触面，Redis 只写过表层用法，消息队列是完全空白 —— 连「实际项目里什么时候该用 MQ」都答不上来。这个空白在面试里是直接扣分项：简历上写了 Redis 缓存，被问「你的缓存和数据库怎么保持一致」就卡住，比没写更糟。

学完之后，简历上关于中间件的每一行都能撑住追问，并且在系统设计环节能主动做出取舍判断而不是背答案。

## Success looks like

- 能把 `app/minimall/cache.py` 的缓存设计讲成一分钟的口头答案：为什么需要 L2 缓存、击穿和穿透的区别、分布式锁为什么用 SETNX + Pub/Sub、熔断器什么时候打开
- 能在白板上画出 Kafka 的一条消息从生产到消费的完整路径，说清分区、消费者组、offset、rebalance 各自解决什么问题，以及为什么要避免 rebalance
- 被问「你的项目为什么不用消息队列」时，能给出**有依据的判断**（同步链路的延迟预算、故障域的代价），而不是「没需求」
- 能对 MySQL / PostgreSQL / MongoDB 的选型给出场景化理由，能说清 Redis 做缓存和做队列的边界在哪
- 能解释中间件的核心保证：持久化、原子性、幂等、顺序、背压 —— 以及它们在具体中间件里各自靠什么机制实现

## Constraints

- 每周 6 小时以上，可承受「一课 + 一次动手练习」的节奏
- 已有 Redis（redis-py 8.0.1 / django-redis 7.0.0）与 MySQL / PostgreSQL 的真实运行环境，项目代码可当教具
- 环境是 Windows + Git Bash，Docker 可用但不是舒适区
- 教学语言中文，技术术语保留英文原词
- Kafka 是 MQ 的第一入口（自己选的），Redis Streams / RabbitMQ 留作后续对比

## Out of scope

- 运维向：集群部署、监控告警、容量规划、K8s operator
- 源码级深挖：Redis 事件循环实现、InnoDB 页结构、Kafka 日志段代码
- MongoDB 暂不作为主线（先专注 MySQL / PostgreSQL 的关系模型对比）
- 高级调优：MySQL 慢查询优化的完整体系、Redis 大 key 治理的全套工具链
- 消息队列的选型战争（Kafka vs Pulsar vs RocketMQ 谁更好）—— 只讲各自解决的问题和代价

---

> 建立于 2026-10-10
