#!/bin/bash
set -euo pipefail

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ROOT="$CHARLOTTE_ROOT/project/menu"
FRONTEND_DIR="$PROJECT_ROOT/ui"

cd "$CHARLOTTE_ROOT"
python -m project.menu.api.main
