#!/bin/bash
set -euo pipefail

# 本地端口: 8100 (前后端共用)

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$CHARLOTTE_ROOT"
python -m project.rag_knowledge.app.api.server
