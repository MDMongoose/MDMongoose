#!/usr/bin/env bash
# Local development without Databricks: in-memory hierarchy with the demo loaded.
#   backend  http://localhost:8000  (MDM_LOCAL=1)
#   frontend http://localhost:5173  (Vite, proxies /api)
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -q -r app/requirements.txt
(cd app && MDM_LOCAL=1 python -m backend.run) &
trap 'kill %1' EXIT
cd app/frontend && npm install --no-audit --no-fund && npm run dev
