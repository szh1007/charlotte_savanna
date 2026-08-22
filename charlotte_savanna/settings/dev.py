"""开发环境设置. 由 manage.py 默认加载."""

from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*"]

# SPA 前端 (Vite dev server, 端口 4300) 经代理访问, 浏览器视角 Origin 为
# localhost:4300 而 Django 见 Host 127.0.0.1:8000, 需放行该 Origin 否则
# 全部 POST 被 CSRF Origin 校验拒绝 (Issue 02 联调关键配置)
# 注: 3001 曾因 Windows 端口排除范围 (Hyper-V/WSL) 不可绑定, 改用 4300
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:4300",
    "http://127.0.0.1:4300",
]

# 开发环境下允许任意跨域 (Django Debug Toolbar 等工具需要)
INTERNAL_IPS = ["127.0.0.1"]
