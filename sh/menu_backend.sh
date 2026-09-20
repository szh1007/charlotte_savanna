#!/bin/bash
set -euo pipefail

# 本地端口: 8001

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ROOT="$CHARLOTTE_ROOT/project/menu"
FRONTEND_DIR="$PROJECT_ROOT/ui"

cd "$CHARLOTTE_ROOT"
python -m project.menu.agent.FAQ.redis_sync
python -m project.menu.agent.milvus_sync
python -m project.menu.api.main
