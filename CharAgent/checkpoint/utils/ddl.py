"""Postgres 的建表语句 (charagent_checkpoints 表): 表结构与索引的**唯一定义处**.

为什么单独一个文件: 这段 SQL 有两个使用方 ——
1. 本包的 Postgres 存储实现 (首次写入时幂等建表, 让 P0 能自己跑起来)
2. issue 08 (P0-7) 的 alembic 迁移 (把正式 schema 纳入版本化管理)
两处必须是**同一份** SQL, 所以放在这里各自 import. 放 utils/ 而不放 postgres.py
的原因: alembic 迁移只需要这段 SQL, 不必连带 import psycopg 那套存储实现.

**表名为什么带 charagent_ 前缀** (而不是就叫 checkpoints): 本项目各子项目共用同一个
Postgres 库 (根 .env 的 PGSQL_*), 而 `langgraph-checkpoint-postgres` 也建了一张叫
`checkpoints` 的表 —— 两边都叫这个名字时, 后者 `setup()` 里的
`CREATE TABLE IF NOT EXISTS checkpoints` 会因为「表已存在」被跳过, 然后它的 INSERT
报「列不存在」, 报错信息跟真正的原因 (表名撞了) 毫无关系, 极难排查. 自己的表带自己
的前缀, 这类撞名从源头消失 (2026-09-13 实测发现).

表结构对齐 02-data-model.md §1 的 Checkpoint 字段表, 三点设计说明:
- **元数据用列存、进度与观察值用 JSON**: thread_id / turn_number 这些要建索引、要
  按条件查 (「这个会话最新一帧是谁」), 所以各自成列; 进度 (消息历史 + 计数器) 与
  观察值 (哪一步最贵、这一帧怎么来的) 内部形状随版本变, 用 JSON 列装, 加字段不必
  改表. 两者分两个列, 是因为「恢复要用的」与「给人看的」该能分开查 (v3 起).
- **parent_id 自引用外键**: 老快照恢复产生的新分支指向老快照, 于是历史是一棵
  树 (time-travel 的回溯路径). 外键保证指向的那份存档真的存在.
  删除动作用 SET NULL 而不是 CASCADE: 删掉一帧时, 挂在它下面的分支**不被连带
  删掉** (只是变成「没有起点的孤儿」) —— 删数据是危险动作, 宁可留下看着奇怪的
  记录, 也不要替调用方把下游数据一起抹掉; 真要整段清理时按 thread_id 一条 SQL
  删干净 (两种情况下删除都能走通).
"""

from __future__ import annotations

CHECKPOINTS_TABLE = "charagent_checkpoints"

# 建表: IF NOT EXISTS 保证重复执行安全 (本包的首次写入会调它, alembic 迁移也会)
CHECKPOINTS_DDL = f"""
CREATE TABLE IF NOT EXISTS {CHECKPOINTS_TABLE} (
    checkpoint_id  TEXT        PRIMARY KEY,
    thread_id      TEXT        NOT NULL,
    run_id         TEXT        NOT NULL,
    turn_number    INTEGER     NOT NULL,
    schema_version INTEGER     NOT NULL,
    state          JSONB       NOT NULL,
    metadata       JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
    parent_id      TEXT        REFERENCES {CHECKPOINTS_TABLE} (checkpoint_id)
                               ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL
)
"""

# 升级语句: 给「更早版本建出来的表」补上新列 (幂等).
#   metadata 列是 v3 加的 —— 建表语句里的 IF NOT EXISTS 管不了已存在的表,
#   所以升级要单独一条. 正式的表结构演进归 issue 08 的 alembic; 这里保证
#   P0 阶段「老表也能直接用」. DEFAULT '{}' 让老行读出来就是「没记过观察值」.
CHECKPOINTS_UPGRADE_DDL = f"""
ALTER TABLE {CHECKPOINTS_TABLE}
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
"""

# 索引: 「按会话翻历史 / 取最新一帧」是唯一的两种查法, 一条复合索引就够
# (created_at + checkpoint_id 一起排, 保证同一毫秒存下的两份存档顺序也确定)
CHECKPOINTS_INDEX_DDL = f"""
CREATE INDEX IF NOT EXISTS idx_{CHECKPOINTS_TABLE}_thread_created
    ON {CHECKPOINTS_TABLE} (thread_id, created_at, checkpoint_id)
"""
