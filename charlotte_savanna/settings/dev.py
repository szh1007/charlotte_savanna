"""开发环境设置. 由 manage.py 默认加载."""

from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*"]

# 开发环境下允许任意跨域 (Django Debug Toolbar 等工具需要)
INTERNAL_IPS = ["127.0.0.1"]
