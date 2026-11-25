#!/usr/bin/env bash
# Optional local build of the React UI (Databricks Apps builds it on deploy anyway).
# Usage: scripts/build.sh [target]   — the catalog and schema names come from the bundle target (databricks.yml)
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-free}"
(cd app/frontend && npm ci --no-audit --no-fund && npm run build)
echo "✓ built — next: databricks bundle deploy -t ${TARGET}"
