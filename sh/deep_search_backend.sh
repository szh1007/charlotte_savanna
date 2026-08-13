#!/bin/bash
set -euo pipefail

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ROOT="$CHARLOTTE_ROOT/project/deep_search"
FRONTEND_DIR="$PROJECT_ROOT/ui"

cd "$CHARLOTTE_ROOT"
python -m project.deep_search.api.server
