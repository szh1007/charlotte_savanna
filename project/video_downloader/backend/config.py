"""应用配置: 从 .env 读取环境变量 (模板见 .env.example)."""

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 (backend/ 的上一级)
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载子项目独立 .env (不提交)
load_dotenv(BASE_DIR / ".env")

# 交付文件目录 (TTL 清理范围, T02+ 使用)
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", BASE_DIR / "downloads"))

# 会员密钥: 校验通过解锁会员档能力 (空 = 未配置, 拒绝一切提交)
MEMBER_KEY = os.getenv("MEMBER_KEY", "")

# 交付直链有效期 (秒): 免费 24h / 会员 72h (PRD §5, T06 清理判定依据)
FREE_DELIVERY_TTL = float(os.getenv("FREE_DELIVERY_TTL", 24 * 3600))
MEMBER_DELIVERY_TTL = float(os.getenv("MEMBER_DELIVERY_TTL", 72 * 3600))


def delivery_ttl(is_member: bool) -> float:
    """交付直链有效期按创建者身份计算 (免费 24h / 会员 72h, PRD §5).

    单一来源: cleaner 过期判定、任务 expires_at 序列化共用, 避免各自
    复制身份分支导致判定漂移.
    """
    return MEMBER_DELIVERY_TTL if is_member else FREE_DELIVERY_TTL
