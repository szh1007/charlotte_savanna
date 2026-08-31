#!/bin/bash
set -euo pipefail

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# localhost:8100
cd "$CHARLOTTE_ROOT"
python -m project.rag_knowledge.app.api.server
