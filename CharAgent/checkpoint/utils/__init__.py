"""checkpoint 的支撑子包: 静态零件都放这里 (对齐 agent/utils 的组织惯例).

- types.py       数据形状: Checkpoint / CheckpointState / Suspension / 能力声明
- errors.py      异常族 (配置 / 序列化 / 迁移 / 能力 / 存储)
- migrations.py  快照格式的版本迁移 (老存档读得回来)
- pending.py     从消息历史里找出「还欠结果的工具调用」(挂起 / 断点恢复用)
- fields.py      从解好的结构里按字段取值 + 校验 (取值报错要说清哪个字段)

域内常量跟随其所属模块, 不上浮到这里: 当前版本号 SCHEMA_VERSION 在 types.py
(它是记录自身的一部分), 序列化的标签键在 serialization.py.

库表结构**不在本子包里** —— 2026-09-14 起统一到 `db/schema.py` (唯一来源),
原 `ddl.py` 已删: 一份定义同时供快照存储建表与 alembic 迁移使用.
"""
