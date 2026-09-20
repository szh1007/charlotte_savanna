#!/bin/bash
set -euo pipefail

# CharApp 客服服务进程 (CharApp/minimall/server.py).
# 本地端口: 1007 (前端复用 Django 8000)
#
# 前置: 运行 Django (python manage.py runserver),
#       根目录下 .env 里配好 CHARAPP_INTERNAL_TOKEN (与 Django 侧同值)
#       与模型 API Key; 监听地址/端口看 CHARAPP_SERVER_* (默认本机 1007).
# 用法: bash sh/charapp_backend.sh          # 前台跑, Ctrl-C 停
#       curl -N -X POST http://127.0.0.1:1007/runs \
#         -H "X-Internal-Token: <令牌>" -H "X-User-Id: 2" \
#         -H "content-type: application/json" -d '{"message":"我余额还有多少"}'

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$CHARLOTTE_ROOT"
python -m CharApp.minimall.server
