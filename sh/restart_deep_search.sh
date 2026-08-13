#!/bin/bash
set -euo pipefail

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ROOT="$CHARLOTTE_ROOT/project/deep_search"
FRONTEND_DIR="$PROJECT_ROOT/ui"

# [STOP] 关闭旧的 5173 端口进程
OLD_PID=$(netstat -ano | grep ":5173" | grep LISTENING | awk '{print $NF}' || true)
if [ -n "$OLD_PID" ]; then
  echo "[DeepSearch] 前端 旧进程已关闭, PID = $OLD_PID"
  taskkill //F //PID "$OLD_PID" >/dev/null 2>&1 || true
  sleep 1
fi

# [RUN] 启动 deep_search 前端进程
mkdir -p "$CHARLOTTE_ROOT/sh/log"
cd "$FRONTEND_DIR"
NO_COLOR=1 nohup npm run dev > "$CHARLOTTE_ROOT/sh/log/deep_search_nohup.out" 2>&1 &

# [CHECK] 通过端口获取 Windows PID
sleep 2
NEW_PID=$(netstat -ano | grep ":5173" | grep LISTENING | awk '{print $NF}' || true)
if [ -n "$NEW_PID" ]; then
  echo "[DeepSearch] 前端 新进程已启动, PID = $NEW_PID"
else
  echo "⚠ 端口 5173 未检测到监听"
fi
