#!/bin/bash
set -euo pipefail

# CharApp 电商客服命令行 (CharApp/minimall).
# 本地端口: 无 (终端程序, 不占端口)
#
# 前置: 商城在跑 (python manage.py runserver), 根 .env 里配好 CHARAPP_INTERNAL_TOKEN.
# 用法: bash sh/charapp_client.sh --user-id 2        # 以买家 #2 的身份进交互模式
#       bash sh/charapp_client.sh --user-id 2 -q "有什么 2000 块以下的商品推荐吗"

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$CHARLOTTE_ROOT"
python -m CharApp.minimall.cli "$@"
