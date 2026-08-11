#!/bin/bash
set -euo pipefail

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# [TEST] mysql_tools
cd "$CHARLOTTE_ROOT"
python -m demo.DeepAgent._4_project_deep_search.tools.mysql_tools
