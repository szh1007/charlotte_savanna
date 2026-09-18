#!/bin/bash
set -euo pipefail

# CharApp 电商客服命令行 (CharApp/minimall), 无独立端口 —— 它是个前台交互程序.
#
# 前置: 商城在跑 (python manage.py runserver), 根 .env 里配好 CHARAPP_INTERNAL_TOKEN.
# 用法: bash sh/charapp_minimall_cli.sh --user-id 3        # 以买家 #3 的身份进交互模式
#       bash sh/charapp_minimall_cli.sh --user-id 3 -q "有什么 2000 块以下的商品推荐吗"

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$CHARLOTTE_ROOT"
python -m CharApp.minimall.cli "$@"
