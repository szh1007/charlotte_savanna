#!/bin/bash
set -euo pipefail

# CharPlot 前端 (project/charplot/frontend), 端口 3001

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FRONTEND_DIR="$CHARLOTTE_ROOT/project/charplot/frontend"

cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
  npm install
fi

npm run dev
