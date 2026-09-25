"""环境变量模板测试: 根 `.env.example` 必须列全代码读的变量名.

为什么要这条: 模板是使用者唯一能照抄的清单 (真 `.env` 不提交). 代码读的变量在
模板里找不到, 使用者就只能翻源码 —— 2026-09-15 审计就发现 checkpoint 与 db 的
十来个变量一个都没写进模板, 而验收要求「存储配置切换可演示」.

变量名一律带 `CHARAGENT_` 前缀 (与 `CHARPLOT_*` / `RK_*` / `MENU_*` 同一套命名法):
本项目各子项目共用同一个根 `.env`, 不带前缀的通名既看不出归属, 也容易撞车.

做法: 拿各配置模块里声明的常量名 (而不是源码里搜字符串) 去比对模板文本 ——
读常量才知道「代码到底认哪些名字」, 搜字符串会把注释与文档里的举例也算进去.

被测对象是「模板与代码的对应关系」, 不连任何外部服务.
"""

from __future__ import annotations

from pathlib import Path

from CharAgent.checkpoint import config as checkpoint_config
from CharAgent.db import config as db_config
from CharAgent.db import cost as db_cost

# 仓库根 (CharAgent/tests/ -> CharAgent/ -> 仓库根)
TEMPLATE = Path(__file__).resolve().parents[2] / ".env.example"

# 由本仓自己读的环境变量名 (按模块分组, 失败信息里好定位是谁家的).
# 两边都从各自的 `config` 模块直取 —— 这些常量刻意**不在**包门面里 (配置内部零件,
# 上浮只会变成看不出归属的通名), 所以测试也跟着走同一条路径.
CHECKPOINT_VARS = (
    checkpoint_config.ENV_BACKEND,
    checkpoint_config.ENV_KEY_PREFIX,
    checkpoint_config.ENV_TTL_SECONDS,
    checkpoint_config.ENV_REDIS_MODE,
    checkpoint_config.ENV_REDIS_MAX_FRAMES,
    checkpoint_config.ENV_REDIS_URL,
    checkpoint_config.ENV_POSTGRES_DSN,
)
DB_VARS = (db_config.ENV_DSN, db_config.ENV_ECHO)
# 成本折算的价目表 (ticket 28): 常量声明在 db/cost.py 里 (读它的也是那个模块)
COST_VARS = (db_cost.ENV_MODEL_PRICES,)


def test_template_lists_every_checkpoint_and_db_variable() -> None:
    """模板里逐个写明了 checkpoint 与 db 读的每个变量名."""
    text = TEMPLATE.read_text(encoding="utf-8")

    missing = [
        name for name in (*CHECKPOINT_VARS, *DB_VARS, *COST_VARS) if name not in text
    ]

    assert missing == [], f".env.example 缺少这些变量名: {missing}"


def test_template_covers_the_shared_postgres_and_redis_names() -> None:
    """共用连接变量也在模板里 (快照与数据层缺专用配置时会回退用它们)."""
    text = TEMPLATE.read_text(encoding="utf-8")

    for name in (
        "PGSQL_USERNAME",
        "PGSQL_PASSWORD",
        "PGSQL_HOST",
        "PGSQL_PORT",
        "PGSQL_NAME",
        "REDIS_URL",
    ):
        assert name in text, f".env.example 缺少 {name} (回退路径要用)"
