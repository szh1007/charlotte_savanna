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
