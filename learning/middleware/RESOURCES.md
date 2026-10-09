# 中间件开发底座 Resources

> 课程里每一个断言都应该能指回这份清单里的某一条。如果我讲的东西不在这里，要么是我在编，要么是该补一条。
>
> **验收状态**（2026-10-10 调研）：标注 ✅ 的由 HTTP 请求实测可达；标注 🔍 的只在搜索结果里出现过、未逐条抓取。不确定的一律标出来，不装。

---

## 知识源

### 一、数据库 — MySQL

- ✅ [MySQL 8.0 Reference Manual · 17.4 InnoDB Architecture](https://dev.mysql.com/doc/refman/8.0/en/innodb-architecture.html)
  官方架构图，一张图串起 buffer pool / change buffer / log buffer / undo 表空间 / redo log。**用在**：面试问「一条 update 经过哪些结构」时从这里起步，顺子章节展开。
- ✅ [17.7 InnoDB Locking and Transaction Model](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-transaction-model.html)
  RC/RR 下的一致读、锁读、gap / next-key 锁、幻读、死锁的**标准答案来源**。**用在**：解释「为什么 RR 下 UPDATE 无索引会锁很多行」必须以此为准 —— 别用二手博客的 5.7 结论。
- ✅ [17.3 InnoDB Multi-Versioning](https://dev.mysql.com/doc/refman/8.0/en/innodb-multi-versioning.html) · [17.6.5 Redo Log](https://dev.mysql.com/doc/refman/8.0/en/innodb-redo-log.html)
  undo + read view 的 MVCC 语义、redo log 结构（8.0.30 起可在线调 `innodb_redo_log_capacity`）。**用在**：分清「undo 管原子性与 MVCC、redo 管持久性、binlog 归 server 层管复制」。
- ✅ [17.6.2.1 Clustered and Secondary Indexes](https://dev.mysql.com/doc/refman/8.0/en/innodb-index-types.html)
  聚簇索引 / 二级索引与「回表」的官方表述。**用在**：回答「主键为什么要短要自增」「为什么函数包列会失效」前先把定义说对。
- ✅ [10.8.1 Optimizing Queries with EXPLAIN](https://dev.mysql.com/doc/refman/8.0/en/using-explain.html)
  EXPLAIN 列含义（含 8.0.18+ 的 EXPLAIN ANALYZE）。**用在**：执行计划里 `Using index condition` 和 `Using index` 到底差在哪 —— 唯一裁量源。
- ✅ [Chapter 19 Replication](https://dev.mysql.com/doc/refman/8.0/en/replication.html)
  binlog 格式、GTID、半同步、组复制。**用在**：讲「主从延迟怎么来的、怎么缓解」时先分清机制与 binlog 章节的分工。

### 二、数据库 — PostgreSQL

- ✅ [Chapter 13 Concurrency Control](https://www.postgresql.org/docs/current/mvcc.html)
  MVCC 与 RC / RR / Serializable（SSI 谓词锁、40001 重试）的规范表述。**用在**：解释「PG 的 RR 为什么报 could not serialize access」—— 比任何二手文章准确。
- ✅ [Chapter 28 Reliability and the Write-Ahead Log](https://www.postgresql.org/docs/current/wal.html) · [28.3 WAL](https://www.postgresql.org/docs/current/wal-intro.html)
  WAL 先行写、LSN、checkpoint 与崩溃恢复。**用在**：讲 WAL 与 InnoDB redo 的相似与差异（LSN vs 段文件、checkpoint 怎么推进）。
- ✅ [24.1 Routine Vacuuming](https://www.postgresql.org/docs/current/routine-vacuuming.html)
  autovacuum 阈值、xid 回卷（wraparound）与 freeze、表膨胀成因。**用在**：回答「PG 为什么需要 VACUUM、不做会怎样」——用官方的公式和阈值，不用博客里的经验值。
- ✅ [14.1 Using EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html) · [Chapter 11 Indexes](https://www.postgresql.org/docs/current/indexes.html)
  EXPLAIN 字段与 BTree / Hash / GiST / GIN / BRIN 的适用范围。**用在**：「为什么 GIN 适合多值列、BRIN 适合有序大表」。
- ✅⭐ [PostgreSQL 14 Internals](https://edu.postgrespro.com/postgresql_internals-14_parts1-4_en.pdf)（英文全书 PDF，免费）· [中文在线版](https://postgres-internals.cn/docs/)（译者获原作者授权）
  Postgres Professional（PG 核心社区公司）出的培训书，**准官方**。Part I 隔离与 MVCC（页/元组、快照、pruning、vacuum、freeze）、Part II 缓冲池与 WAL、Part III 锁、Part IV–V 执行与索引类型。**用在**：把 PG 从「会用」拉到「懂原理」的主干教材 —— 面试前串讲 MVCC + VACUUM 直接读 Part I。
- ✅ [Architecture of a Database System](https://dsf.berkeley.edu/papers/fntdb07-architecture.pdf)（Hellerstein / Stonebraker / Hamilton, 2007）· [中文版（厦大林子雨团队译）](http://dblab.xmu.edu.cn/node/459)
  数据库领域的学术 canonical（MIT 6.830 指定阅读）。进程模型、存储管理、事务系统、查询处理器的整体分层，重点看 "The Life of a Query" 一节。**用在**：面试要你「从上到下画一张 DBMS 地图」时，把零散知识点挂到骨架上。成文 2007，不含云原生形态。

### 三、数据库 — MongoDB

- ✅ [Data Modeling](https://www.mongodb.com/docs/manual/data-modeling/) · [Indexes](https://www.mongodb.com/docs/manual/indexes/)｜[中文站](https://www.mongodb.com/zh-cn/docs/manual/)
  embed vs reference 的建模判据、ESR 规则（equality-sort-range）、复合/多键/TTL/通配索引。**用在**：「这个文档模型该内嵌还是引用」「复合索引字段顺序怎么定」。
- ✅ [Replication](https://www.mongodb.com/docs/manual/replication/) · [Read Concern](https://www.mongodb.com/docs/manual/reference/read-concern/) · [Write Concern](https://www.mongodb.com/docs/manual/reference/write-concern/)
  副本集角色、选举与 oplog、local/majority/snapshot/linearizable 读关注、`w:majority` + `j:true` 写关注的一致性代价。**用在**：被追问「majority 读到底保证什么、和 causal consistency 什么关系」。
- ✅ [Transactions](https://www.mongodb.com/docs/manual/core/transactions/)
  多文档事务的写法、限制（时长、大小、跨分片）与事务内的 read/write concern。**用在**：「MongoDB 事务和关系库差在哪、什么时候不该用」的官方边界清单。

### 四、数据库 — 跨库与索引

- ✅ [InnoDB（Jeremy Cole 系列博客）](https://blog.jcole.us/innodb/)
  前 MySQL 团队工程师，同时开源 `innodb_ruby` / `innodb_diagrams`，社区公认 InnoDB 磁盘结构最好的解读之一。记录头（`DB_TRX_ID` / `DB_ROLL_PTR`）、页结构、undo 版本链的物理布局。**用在**：需要比官方文档深一层、但还没到读源码的图景时。非官方，引用时标注版本。
- ✅ [Use The Index, Luke!](https://use-the-index-luke.com/)（Markus Winand）
  索引领域的 community canonical（*SQL Performance Explained* 的在线版），按 MySQL / PG / Oracle / SQL Server 分注。B+Tree 解剖、复合索引最左前缀、函数包列失效、分页与排序。**用在**：需要**实验室级、可复现**地证明「为什么这个查询用/不用索引」。
- ✅ [图解MySQL](https://xiaolincoding.com/mysql/)（小林coding，中文）｜[JavaGuide MySQL 专题](https://javaguide.cn/database/mysql/)
  中文面试圈口碑最好的图解教程 + 15.8 万 star 的开源题库。**诚实定位：二手整理级** —— 覆盖面好、更新活跃，但深度停在问答层面、个别条目版本滞后。**用在**：快速定位「我还不会什么」，每个疑点回官方文档核准，**不作最终依据**。

### 五、缓存 — Redis 官方文档

- ✅ [Persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
  RDB（fork + COW）、AOF（`appendfsync always/everysec/no`）、AOF rewrite、混合持久化、备份与灾难恢复。**用在**：面试必问的「RDB vs AOF 怎么选、能丢多少数据」。
- ✅ [Key eviction](https://redis.io/docs/latest/develop/reference/eviction/)
  `maxmemory` 与淘汰策略（noeviction / allkeys-* / volatile-* 的 LRU、LFU、random、TTL 变体）、近似 LRU/LFU 的采样实现。**用在**：为什么 `volatile-*` 在内存写满时仍可能失败。
- ✅ [Memory optimization](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization/)
  编码阈值、内存碎片、为什么小对象省内存。与上一条连读即覆盖内存管理。
- ✅ [Diagnosing latency issues](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/latency/)
  内含官方**唯一成节**解释：「Single threaded nature of Redis」，外加各类延迟来源（慢命令、fork、swap、THP）。**用在**：「为什么单线程还快」的第一去处。
- ✅ [Redis FAQ](https://redis.io/docs/latest/develop/get-started/faq/) — 条目 "How can Redis use multiple CPUs?"
  官方原文：CPU 不是瓶颈，内存和网络才是。补上「单线程」那一课的另一半。
- ✅ [Diving into Redis 6](https://redis.io/blog/diving-into-redis-6/)
  6.0 引入 I/O 多线程（**命令执行仍然单线程**）、ACL、客户端缓存。**用在**：八股文普遍停留在「Redis 是单线程的」，这一条补上 6.0+ 的变化 —— 面试里说出来就是区分度。
- ✅ [Replication](https://redis.io/docs/latest/operate/oss_and_stack/management/replication/) · [Sentinel](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/) · [Cluster](https://redis.io/docs/latest/operate/oss_and_stack/management/scaling/) · [Cluster specification（进阶）](https://redis.io/docs/latest/operate/oss_and_stack/reference/cluster-spec/)
  PSYNC 与复制积压、选主与 quorum、16384 槽与重分片。三页覆盖基本盘，spec 页是加餐。
- ✅ [Data types](https://redis.io/docs/latest/develop/data-types/) · [Commands（含 O() 复杂度标注）](https://redis.io/docs/latest/commands/)
  随手查语义与复杂度的地方。**用在**：确认某个命令的复杂度时 —— 面试里「为什么 `KEYS` 危险、`SCAN` 好在哪」的最终依据。
- ✅ [Transactions](https://redis.io/docs/latest/develop/using-commands/transactions/) · [Pipelining](https://redis.io/docs/latest/develop/using-commands/pipelining/) · [Scripting with Lua](https://redis.io/docs/latest/develop/programmability/eval-intro/) · [SCAN](https://redis.io/docs/latest/commands/scan/) · [Keyspace notifications](https://redis.io/docs/latest/develop/pubsub/keyspace-notifications/)
  五篇短文，各 20 分钟。MULTI/EXEC/WATCH 的**无回滚语义**、RTT 与吞吐、脚本的原子性、游标保证、通知的投递语义（不持久化）—— 全是面试高频。
- ✅ [redis.conf 的注释原文](https://github.com/redis/redis/blob/unstable/redis.conf)
  配置文件自带的长注释等于一份**运维者向导**：内存、持久化、复制、集群、延迟旋钮全在里面。**用在**：读完上面几章后扫一遍，把章节知识和实际参数对上。

### 六、缓存 — Redis 深挖与中文

- ✅ [Redis persistence demystified](https://oldblog.antirez.com/post/redis-persistence-demystified.html)（antirez 亲笔）
  AOF fsync 的 group commit、持久化语义边界。同站 [A few key problems in Redis persistence](https://oldblog.antirez.com/post/a-few-key-problems-in-redis-persistence.html) 讲 COW 最坏 2× 内存、AOF rewrite 为什么难。**用在**：官方文档只给结论、不给取舍理由的地方。
- ✅ [Streams 官方文档](https://redis.io/docs/latest/develop/data-types/streams/) · [Streaming 用例页](https://redis.io/docs/latest/develop/use-cases/streaming/)
  `XADD` / `XREADGROUP` / `XACK` / `XAUTOCLAIM` / PEL。**用例页直接与 Kafka/Pulsar 对比**（中等规模、短保留期可用 Streams 替代，否则上 Kafka）—— 正好是后面做 MQ 选型对比时的官方口径。
- ✅ [Streams: a new general purpose data structure in Redis](https://antirez.com/news/114) · [Redis streams as a pure data structure](https://antirez.com/news/128)（设计者亲述）
  设计动机：基数树、ID 设计、与 pub/sub 和 zset 的取舍。**用在**：问「为什么 Redis 要再造一个 MQ」。
- ✅ [《Redis 设计与实现》在线全文](https://huangz.works/redisbook/) · [新版读者站](https://huangz.works/redisbook1e/)（黄健宏，中文，免费）
  中文 Redis 原理书的**事实标准**。SDS / dict 增量 rehash / skiplist / 事件循环 / AOF·RDB / 复制·哨兵·集群。**用在**：「懂原理」最省力的一本。**但停在 Redis 3.0** —— 无 Streams、无 6.0 I/O 线程，与官方文档搭配互补。
- ✅ [Redis 内部数据结构详解](http://zhangtielei.com/posts/blog-redis-dict.html)（张铁蕾，中文）· [skiplist 篇](http://zhangtielei.com/posts/blog-redis-skiplist.html)
  把「哈希表怎么增量 rehash」（面试高频）和「跳表为什么这么设计、与平衡树的取舍」讲透。基于 3.x，编码细节以官方为准。
- ✅ [图解Redis](https://xiaolincoding.com/redis/)（小林coding，中文）｜[JavaGuide Redis 面试题上](https://javaguide.cn/database/redis/redis-questions-01.html) · [下](https://javaguide.cn/database/redis/redis-questions-02.html)
  中文面试话术与索引。**诚实定位：二手**，部分页面版本滞后（listpack 等），事实以官方文档为准。

### 七、消息队列 — 概念层（为什么存在）

- ✅ [The Log: What every software engineer should know about real-time data's unifying abstraction](https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying)（Jay Kreps, 2013）｜[中文译本目录](https://github.com/oldratlee/translations)
  Kafka 作者本人。把「日志」这个抽象讲透 —— MQ、复制、CDC、流处理的共同底层。**用在**：回答「为什么 Kafka 是 log 而不是 queue」—— 从这篇起步。⚠️ 原站常失效，中文译本更稳。
- ✅ [Enterprise Integration Patterns · Messaging 目录](https://www.enterpriseintegrationpatterns.com/patterns/messaging/toc.html)｜[Dead Letter Channel](https://www.enterpriseintegrationpatterns.com/patterns/messaging/DeadLetterChannel.html)｜[Guaranteed Messaging](https://www.enterpriseintegrationpatterns.com/patterns/messaging/GuaranteedMessaging.html)
  DLQ、投递保证、幂等接收者这些词的**原始出处**（Hohpe & Woolf）。⚠️ 是 `GuaranteedMessaging.html`，不是 `GuaranteedDelivery`。**用在**：回答里用对术语、显得有根。
- ✅ [Transactional Outbox](https://microservices.io/patterns/data/transactional-outbox.html) · [Polling Publisher](https://microservices.io/patterns/data/polling-publisher.html)（Chris Richardson）
  「数据库写成功但消息没发出去」这个双写问题的标准解法，模式命名者本人写的。**用在**：面试写路径题的高频考点 —— 这是 MQ 与数据库交界处最重要的一课。
- ✅ [Event-Driven Architectures — The Queue vs The Log](https://jack-vanlightly.com/blog/2018/5/20/event-driven-architectures-the-queue-vs-the-log)（Jack Vanlightly, 2018）
  前 RabbitMQ 核心团队 / IBM 研究员写的**中立**分类：队列型（RabbitMQ/SQS）vs 日志型（Kafka/Kinesis）。**用在**：同类「Kafka vs RabbitMQ」内容绝大多数是厂商营销，这是少见的独立视角。
- ✅ [Designing Data-Intensive Applications 第 11 章 Stream Processing](https://dataintensive.net/)（Kleppmann，付费书）｜[中文版第 11 章在线](https://www.oreilly.com/library/view/she-ji-shu-ju-mi-ji-xing-ying-yong-cheng-xu/9798341656581/ch11.html)
  投递语义（at-most-once / at-least-once / exactly-once，以及 "effectively-once" 这个更诚实的措辞）、log-based broker、offset 与消费组、CDC 的**最系统化讲解**。**用在**：语义那一课的主干。付费，但值。

### 八、消息队列 — Kafka 深水区

- ✅ [Kafka: a Distributed Messaging System for Log Processing](https://www.microsoft.com/en-us/research/wp-content/uploads/2017/09/Kafka.pdf)（Kreps / Narkhede / Rao, NetDB 2011）· [官方论文索引](https://kafka.apache.org/books-and-papers)
  奠基论文。**⚠️ 三条必须知道的时效警告**：① 此文**尚无副本机制**（原文明写 replication 是 future work）；② 依赖 ZooKeeper，而 4.0 已移除、改 KRaft；③ 生产端语义与现在不同。**用在**：讲设计动机与取舍（pull 模型、批处理、零拷贝、无中心 master）—— 那是它仍然准确的部分。**别拿它当现状去面试。**
- ✅ [Building a Replicated Logging System with Apache Kafka](https://vldb.org/pvldb/vol8/p1654-wang.pdf)（Wang et al., VLDB 2015）
  补上 2011 论文缺的那一块：复制（primary-backup vs quorum、ISR、leader election 分离）。**用在**：「ISR 为什么这么设计、和 Raft 的 quorum 差在哪」。
- ✅ [Apache Kafka 官方文档 · Introduction](https://kafka.apache.org/43/getting-started/introduction/)｜[Design](https://kafka.apache.org/43/design/)｜[Implementation](https://kafka.apache.org/43/implementation/)｜[Operations](https://kafka.apache.org/43/operations/)｜[Quickstart](https://kafka.apache.org/quickstart)
  Design 章讲持久化、效率、投递语义、复制、日志压缩；Operations 讲扩容与关键配置。**注意站点结构已改版**：4.x 起文档按版本号路径组织（`/43/...`），网上 3.x 时代的锚点链接大多失效。**用在**：原理的最终依据。
- ✅ [Exactly-once Semantics are Possible: Here's How Kafka Does it](https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/) · [Transactions in Apache Kafka](https://www.confluent.io/blog/transactions-apache-kafka/)
  幂等生产者（PID + 序列号 + 去重窗口）、事务（`transactional.id`、`read_committed` / LSO、僵尸 fencing）。**作者是 Kafka 作者本人**，但**是厂商内容** —— 读结论，审视立场。**用在**：「exactly-once 到底保证什么、不保证什么（外部系统副作用不在保证内）」的标准答案。
- 🔍 [KIP-848: The Next Generation of the Consumer Rebalance Protocol](https://cwiki.apache.org/confluence/display/KAFKA/KIP-848%3A+The+Next+Generation+of+the+Consumer+Rebalance+Protocol)｜[Confluent 解读](https://www.confluent.io/blog/kip-848-consumer-rebalance-protocol/)
  新 rebalance 协议（服务端协调、无全局 stop-the-world），Kafka 4.0 GA。**用在**：「rebalance 为什么慢、4.0 改了什么」。⚠️ cwiki 正文页未直接打开验证，URL 为标准形式。
- ✅⭐ [Confluent Developer 免费课程](https://developer.confluent.io/courses/)｜[Kafka 101](https://developer.confluent.io/courses/apache-kafka/events/)｜[10 分钟第一个应用](https://developer.confluent.io/courses/apache-kafka/get-started-hands-on/)｜[Architecture 课程 · Transactions 模块](https://developer.confluent.io/courses/architecture/transactions/)
  **零成本最高性价比的一站式路径**。Kafka 101 解决「会用 / 为什么用」；Architecture 课程（作者 = Kafka 共同作者 Jun Rao）解决「懂原理」：broker 请求全流程、复制协议（ISR / leader epoch / HW）、KRaft 控制面（含设计历史，最接近「论文回顾」的东西）、消费组协议与 rebalance、`acks` / `min.insync.replicas` 权衡、事务、日志压缩。⚠️ 部分实验依赖 Confluent Cloud 注册。
- 🔍 [Kafka: The Definitive Guide（免费电子书）](https://www.confluent.io/resources/ebook/apache-kafka-the-definitive-guide/)｜[第 2 版（需邮箱注册）](https://www.confluent.io/resources/kafka-the-definitive-guide-v2/)
  O'Reilly，作者含 Confluent 工程师。producer / consumer internals、Reliable Data Delivery、EOS 各一章。第 2 版（2021）纳入 AdminClient、事务、安全。⚠️ 第 2 版链接未经直接验证。

### 九、消息队列 — 面试题与中文生态

- ✅ [Hello Interview: Kafka Deep Dive for System Design Interviews](https://www.hellointerview.com/learn/system-design/deep-dives/kafka)
  前 Meta staff engineer 团队。何时用 Kafka、partition key 与 hot partition、retry topic / DLQ（**Kafka 无原生 DLQ**，与 SQS 对比）、retention、`acks` 与复制。**用在**：直接对应「顺序 / 重复 / 积压 / 丢消息」四大追问。**二手**但质量高、口径贴近真实考察。
- ✅ [JavaGuide 消息队列专题](https://www.javaguide.cn/high-performance/message-queue/message-queue-interview-questions.html) · [doocs/advanced-java · 为什么使用 MQ](https://github.com/doocs/advanced-java/blob/main/docs/high-concurrency/why-mq.md) · [标签页](https://javaguide.cn/tag/%E6%B6%88%E6%81%AF%E9%98%9F%E5%88%97/)
  中文面试上下文的标准骨架（解耦 / 异步 / 削峰 + 可靠性 / 幂等 / 顺序 / 积压 / 选型）。**诚实定位：二手** —— 用来对齐「国内怎么问」，事实细节回官方文档核。
- ✅ [美团技术团队 · 数据平台 Kafka 实践](https://tech.meituan.com/2022/08/04/The-Practice-of-Kafka-In-the-Meituan-Data-Platform.html) · [Kafka SSD 应用](https://tech.meituan.com/2021/01/14/kafka-ssd.html) · [Kafka 标签页](https://tech.meituan.com/tags/kafka.html)
  一线大厂在生产规模下的慢节点、PageCache 污染、迁移容灾。**用在**：面试聊「大规模 MQ 运维 / 性能」时**近乎唯一的真实素材** —— 英文资料没有这个规模的描述。
- 💰 [极客时间 ·《Kafka 核心技术与实战》胡夕](https://time.geekbang.org/column/intro/100029201)｜[《深入理解 Kafka》朱忠华](http://www.broadview.com.cn/book/5919)
  **付费**，但属中文生态的 canonical（胡夕是 Apache Kafka Committer；朱书是中文最系统的原理书）。⚠️ 两者均基于 4.0 / KRaft 之前。**只在**免费来源不够时买。

---

## Gaps（明确没有好来源的地方 —— 这些地方我会保守，并且标出来）

**数据库**
- MySQL 的 **undo 版本链 / Read View 实现级细节**：官方只有一页薄描述，免费且权威的深入材料基本不存在。
- **InnoDB 精确加锁行为**（next-key、插入意向锁、唯一键冲突锁）：官方只给结论表格，系统性推导散落在付费专栏里；中文圈大量文章混用 5.7 与 8.0 结论 —— 引用前必须核版本。
- **PostgreSQL 中文一手资料**：官方手册中文版是社区志愿翻译，权威但有滞后。「current」版中文通常落后英文一截。
- **MongoDB WiredTiger 内部**（并发控制、checkpoint、cache eviction）：缺少像 PG14 Internals 那样成体系的免费材料。
- **「EXPLAIN → 改索引 → 复测」的完整闭环教程**：官方只解释字段含义，成体系的调优训练基本在付费书里。
- **隔离级别异常的可复现实验集**：免费且系统化的很少。

**缓存**
- **没有官方中文文档**（`redis.io/zh` 实测 404）；唯一整站中文镜像自我声明非官方。中文读者只能靠二手，都有版本滞后。
- **Redis 6/7/8 新特性的「懂原理」资料断档**：《Redis 设计与实现》停在 3.0、antirez 博文停在 2020 前、八股站版本漂移（listpack、I/O 线程、客户端缓存、functions、ACL）。只能靠官方 docs + release notes 自己拼。
- **「单线程」的官方解释很短**：只有 latency 文档一节 + FAQ 一条，没有独立章节；中间层权威解读找不到。
- **Cluster / Sentinel 的内部机制**只有密集的 spec，缺教程级读物。

**消息队列**
- **免费 hands-on lab 很薄**：官方 Quickstart 需本地 JVM/Docker；Confluent 的练习依赖注册。**中文 hands-on 基本为零。**
- **中立的三方对比稀缺**：Kafka vs RabbitMQ vs Redis Streams 的同篇权威对比不存在 —— 各家的对比页都是营销。Redis Streams 那一边由 Redis 官方页补上（见第五、六节）。
- **2011 论文没有官方回顾文**：「KRaft 移除 ZooKeeper 后哪些结论作废」没有单一出处，要自己对照 docs。最接近的是 Jun Rao 课程的设计历史模块 + VLDB 2015 + The Log。
- **Kafka 原生能力缺口**（无原生 DLQ / 优先级 / 延迟消息）内容零散在 Confluent blog 与 Hello Interview 里。
- **官方文档无中文版**：中文世界依赖翻译书、付费课与 SEO 站点。

**方法论备注**：本次调研中 `WebFetch` 被网络策略拦截，验证手段 = 搜索结果中的真实链接 + 直接抓取。Firecrawl 账户 credits 偏低，后续大批量抓取前要注意。

---

## 智慧（社区）

> 知识从文档来，**智慧只能从「跟运维过这些系统的人对话」来**。下面这一节的分工是：英文社区给你**深度与规模**，中文社区给你**国内面试口径与可达性**。
>
> ⚠️ **访问现实先说清**：V2EX 主域在国内需要代理；知乎要登录（通常 +86 手机号）；多数中文平台注册要国内手机号；英文邮件列表和 Slack 只需要一个邮箱。这条决定了你**实际**会用到哪些。

### 中文社区（可达性优先）

- [墨天轮 modb.pro](https://www.modb.pro/) · [问答区](https://www.modb.pro/ask)
  中文**数据库从业者**密度最高的社区（Oracle / MySQL / PostgreSQL / 国产库），有博客、文库、大会内容。**用在**：DBA 视角的实战经验、国产库（OceanBase / GaussDB / TiDB）的真实落地报告、看运维怎么想问题。**局限**：应用侧的缓存 / MQ 设计偏少。浏览免登录，发帖要注册。
- [V2EX · /go/db](https://www.v2ex.com/go/db) · [/go/mysql](https://www.v2ex.com/go/mysql) · [/go/redis](https://www.v2ex.com/go/redis) · [/go/kafka](https://www.v2ex.com/go/kafka) · [免代理镜像（仅浏览）](https://global.v2ex.co/)
  真实在职工程师，观点鲜明甚至刻薄，但**问得具体就有高质量回答**。**用在**：「这东西真有人上生产吗」这类现实校验。MQ 节点比数据库薄。⚠️ 主域登录发帖需要代理。
- [知乎](https://www.zhihu.com/)
  头条答案常有云厂商内核团队的人写，**但评论区往往才是真话**。**用在**：「为什么某产品在生产里表现成那样」深度回答、故障复盘。⚠️ 匿名访问被挡，要登录；信息噪声大。
- [掘金](https://juejin.cn/)
  **诚实定位：量大有噪声**。有工程师的好文，也混着大量八股、引流公众号 / 卖课、同题泛滥（社区自己就吐槽「卖课和引流的天下了」）。**用在**：按关键词搜某一篇具体技术总结，**不用来提问**。
- [SegmentFault 思否](https://segmentfault.com/questions)
  活跃度已下降，高威望用户治理被批评偏弱。**用在**：需要中文提问但不需要深度专家的时候；翻老的高票问答。
- [linux.do](https://linux.do/)
  **注册有门槛**（要 LV3 邀请，或 GitHub 账号 3 年以上 + 申请短文），但明确禁广告禁戾气、国内直连、回复快。**用在**：快问快答、找社区。⚠️ 内容偏 AI / 通用开发，**数据库和 MQ 深度有限**。
- 供应商社区：[阿里云开发者社区 · 数据库](https://developer.aliyun.com/database/) · [腾讯云开发者社区 · 问答](https://cloud.tencent.com/developer/ask)
  **不是中立社区**（有产品推广和内容农场帖），但**用在**：想知道云托管的具体版本行为、迁移升级实践时，它们是唯一有料的。
- [PGFans](https://www.pgfans.cn/qa) · [PostgreSQL 中文社区](http://www.postgres.cn/)
  PG 专项。前者自述「全国唯一的 PostgreSQL 问答社区」（活跃度未验证，估计小）；后者主要是文档镜像和门户，**互动很弱**。
- [ITPUB](https://www.itpub.net/forum.php) · [ChinaUnix](http://bbs.chinaunix.net/)
  **当档案库用，不当社区用**。ITPUB 多数版面 2023–2024 就停了，少数 Oracle / ERP 版面活跃到 2026；ChinaUnix 同理。**用在**：考古挖老帖。

### 英文社区（深度与规模）

- [r/apachekafka](https://www.reddit.com/r/apachekafka/)（约 2 万成员）
  单一系统里从业者密度最高的板块，反厂商水军管得严。**用在**：review 你的 partition 数决策、consumer lag / rebalance 诊断、KIP 讨论、集群规模战争故事。⚠️ Reddit 在我这边网络不通，规模和规则来自搜索元数据，未直接抓取。
- [r/dataengineering](https://www.reddit.com/r/dataengineering/)（约 47.8 万成员）
  最大的数据工程社区，真实生产架构讨论与入门求职内容并存，禁招聘帖。**用在**：技术栈选型的现实校验（批 vs 流、warehouse / lake）、读生产事故复盘。**不适合**：数据库内部原理。
- [r/PostgreSQL](https://www.reddit.com/r/PostgreSQL/)（约 8.8 万成员）
  在 Reddit 里算很技术的：备份工具、调优、迁移、扩展。初学者问题被容忍。
- [r/redis](https://www.reddit.com/r/redis/)（约 1–1.4 万成员）
  小但真，有「我为什么选 Redis Streams 而不是 Kafka」这类设计讨论。**回答偏慢**。
- [r/ExperiencedDevs](https://www.reddit.com/r/ExperiencedDevs/)（约 41.6 万成员）
  ⚠️ **这个板块的定位是「按规则不欢迎新手」** —— 版规第 1 条：经验不足 3 年不得发帖和评论（只有每周的 Ask Experienced Devs 帖开放）。**用在**：拿架构取舍去压测那些运维过多年系统的人的判断；读事故与文化战争故事。你的年限符合，但语气直，别问泛问题。
- [r/mysql](https://www.reddit.com/r/mysql/)（约 5 万成员）
  MySQL 运维与使用问答。国内 MySQL 出场率高，值得跟。

### 邮件列表（官方、低噪、开发者本人看）

- **Kafka dev list** — `dev@kafka.apache.org` · [归档](https://lists.apache.org/list?dev@kafka.apache.org)
  ⭐ **这是拿来读的，不是拿来问的**。KIP 辩论记录的是「Kafka 为什么长成这样」的思考过程 —— 一个问题的设计理由、被否决的替代方案、当时的权衡，全在里面。这是**最接近「论文回顾文」的东西**（而 2011 论文恰恰缺这个）。
- **Kafka users list** — `users@kafka.apache.org` · [归档](https://lists.apache.org/list?users@kafka.apache.org)
  用户提问区，流量适中但**有人答**。纯文本邮件，订阅要回信确认。
- **PostgreSQL 列表** — [总览](https://www.postgresql.org/list/) · [订阅](https://lists.postgresql.org/)
  `pgsql-performance`（查询计划 / 调优，**发问前必读** [Guide to reporting problems](https://wiki.postgresql.org/wiki/Guide_to_reporting_problems) 与 [Slow Query Questions](https://wiki.postgresql.org/wiki/Slow_Query_Questions)）· `pgsql-general`（有开发者驻场）· `pgsql-novice`（「没有问题是太简单的」）· `pgsql-hackers`（PG 开发者住这儿，读了学原理；发帖要先「别处问过了」）。
  ⚠️ `pgsql-zh-general` 存在，但**流量极低**（2026 年 8 月之后就没动静了），别指望快回复。

### 聊天与论坛（快问快答）

- [Confluent Community Forum](https://forum.confluent.io/)
  **永久的、可搜索的** Kafka / Flink / Connect 问答，有 Confluent 工程师和 committer 回答。⚠️ 是真的 `forum.confluent.io` —— `discuss.confluent.io` 不存在。
- [Confluent 社区 Slack（从这里进）](https://developer.confluent.io/community/ask-the-community/)
  实时。⚠️ **Slack 历史约两周就没了** —— 深问题发论坛，不在这儿问。另外**没有官方 Apache Kafka Slack**，这个 Confluent 托管的就是事实上的那个。
- Redis 三件套：[论坛](https://forum.redis.io/) · [Discord](https://discord.gg/redis) · [GitHub Discussions](https://github.com/redis/redis/discussions)
  **用在**：语义细节、版本变更问题、读维护者的回复。
- [RabbitMQ Discord](https://www.rabbitmq.com/discord) · [GitHub Discussions](https://github.com/rabbitmq/rabbitmq-server/discussions)
  **用在**：MQ 可靠性语义、quorum queue、集群故障。
- [PostgreSQL Slack](https://www.postgresql.org/community/)（邀请链接在官方社区页）· [IRC](https://www.postgresql.org/community/irc/)（Libera Chat `#postgresql`）
  实时求助。
- [Percona Community Forum](https://forums.percona.com/)
  厂商运营但实用，MySQL / PG / MongoDB 运维经验，备份与复制的好料。

### 问答档案

- [Stack Overflow · apache-kafka](https://stackoverflow.com/questions/tagged/apache-kafka)（33,197 题）· [postgresql](https://stackoverflow.com/questions/tagged/postgresql)（178,777 题）· [redis](https://stackoverflow.com/questions/tagged/redis)（25,403 题）· [rabbitmq](https://stackoverflow.com/questions/tagged/rabbitmq)（14,313 题）
  **先搜，别急着问** —— 档案本身就是智慧。发帖是「有最小可复现例子否则关帖」的硬门槛，历史上对模糊的新手问题不友好，但那是规则问题不是语言问题，非母语者在这儿很正常。
- [dba.stackexchange.com](https://dba.stackexchange.com/)(postgresql 17,590 题)
  DBA 向问题（备份、复制、权限、参数）。

### 演讲档案（会议录像 = 别人替你踩过的坑）

- [Kafka Summit 录像存档](https://kafka.apache.org/community/videos/)（2018 年起，免费）
  **用在**：听 LinkedIn / Netflix / Uber 的工程师讲运维战争故事和设计理由 —— 这是「设计动机」最好的补充材料。
- [PGConf.dev](https://pgconf.dev/)（录像汇总在 [cotalks.dev](https://cotalks.dev/channels/@pgconfdev)）· [PGConf.EU 2026](https://2026.pgconf.eu/)（2025 全部录像免费，官方频道 `youtube.com/@pgeu`）
  **用在**：PG 内部原理、贡献者级别的分享。
- 中文：[QCon 中国](https://qcon.infoq.cn/) · [ArchSummit](https://archsummit.infoq.cn/) · [InfoQ 中国免费视频](https://www.infoq.cn/video) · [DTCC 中国数据库技术大会](https://dtcc.it168.com/)
  **用在**：中文体系的架构实践演讲；DTCC 是数据库专项，内容常在 ITPUB / 墨天轮二次传播。

### 明确不建议当「社区」用的

- **CSDN** —— 中文搜索排名高，但长期被指出抄袭、广告、内容质量不稳。**只在**贴错误信息快速搜时用，**别当社区**。
- **牛客网** —— 面经 / 笔试题库 / 招聘平台。**用在**：了解面试形式和公司 JD 信号，**别的东西一概没有** —— 生产智慧为零。这是「八股 + 引流」那一类，只作为唯一一个面试向条目保留。

### 社区里的言行须知（新手 / 非母语友好度）

| 类型 | 谁 | 说明 |
|------|-----|------|
| **按规则不欢迎新手** | r/ExperiencedDevs、Stack Overflow | 前者要 3 年经验，后者要最小可复现例子 |
| **对低质量提问直接、但问得好就认真答** | r/apachekafka、PostgreSQL 邮件列表、pgsql-hackers | 发问前先读官方的「怎么报告问题」 |
| **友好** | Confluent 论坛 / Slack、Redis Discord / 论坛、RabbitMQ Discord、linux.do、墨天轮 | 问得具体就有人帮 |
| **非母语英语** | 英文技术 subreddit / 邮件列表 | 大量非母语者，语法错被容忍；但邮件列表要求纯文本和精确的问题描述 |

### 未能验证项（如实列出）

Reddit 与 YouTube 在我的沙箱里不可达，相关条目（成员数、版规、录像频道）来自搜索元数据与归档，**未直接抓取**；PostgreSQL Slack 邀请链接由官方社区页发布但流程返回 403 未能走通；知乎的版面 ID 无法匿名验证；linux.do 抓取被挡；PGFans 活跃度无法确认；RocketMQ 的 Slack 自动邀请（Heroku 那个）**已失效**，改用邮件列表。

---

## 推荐阅读路径

> 这是**路线**不是**课表**。具体每一课教什么，由 `learning-records/` 里的起点数据决定。

1. **建立「为什么存在」的直觉** —— Confluent Developer Kafka 101 + 美团实践文。先有场景感，再谈机制。
2. **补原理** —— Kafka 官方 docs（Design 章）+ Jun Rao 的 Architecture 课程。
3. **提升回答的抽象层次** —— Jay Kreps 的 *The Log* + Enterprise Integration Patterns。
4. **对齐面试口径** —— Hello Interview + JavaGuide（**只用来对齐问法，事实回官方核**）。
5. **数据库与缓存穿插** —— 按 `learning-records/` 里暴露出来的缺口决定顺序，不按这份清单的顺序。
