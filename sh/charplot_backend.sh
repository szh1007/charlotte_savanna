#!/bin/bash
set -euo pipefail

# CharPlot FastAPI AI 能力端 (project/charplot), 端口 8004

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$CHARLOTTE_ROOT"
python -m project.charplot.api.server
