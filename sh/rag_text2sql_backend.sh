#!/bin/bash
set -euo pipefail

# 本地端口: 8200

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ROOT="$CHARLOTTE_ROOT/project/rag_text2sql"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

cd "$PROJECT_ROOT"
python -m uvicorn main:app --host 127.0.0.1 --port 8200 --reload
