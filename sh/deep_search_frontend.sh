#!/bin/bash
set -euo pipefail

# 本地端口: 9002

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ROOT="$CHARLOTTE_ROOT/project/deep_search"
FRONTEND_DIR="$PROJECT_ROOT/ui"

cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
  npm install
fi

npm run dev
