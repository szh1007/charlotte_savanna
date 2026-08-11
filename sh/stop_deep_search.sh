#!/bin/bash
set -euo pipefail

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# [STOP] 关闭 5173 端口进程
OLD_PID=$(netstat -ano | grep ":5173" | grep LISTENING | awk '{print $NF}' || true)
if [ -n "$OLD_PID" ]; then
  echo "[DeepAgent-DeepSearch] 前端进程已关闭, PID = $OLD_PID"
  taskkill //F //PID "$OLD_PID" >/dev/null 2>&1 || true
else
  echo "[DeepAgent-DeepSearch] 端口 5173 无监听进程，无需停止"
fi
