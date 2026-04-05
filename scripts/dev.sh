#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

pip install -e ".[dev]"

python -m cloud_engineer_mcp serve \
    --config config.yml \
    --transport both \
    --log-level DEBUG
