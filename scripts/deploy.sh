#!/usr/bin/env bash
# One-shot deploy to a workspace (Free Edition works): build, deploy, initialise, start the app.
# Usage: scripts/deploy.sh [catalog] [target]       (defaults: workspace free)
set -euo pipefail
cd "$(dirname "$0")/.."
CATALOG="${1:-workspace}"
TARGET="${2:-free}"
scripts/build.sh "$TARGET"
databricks bundle deploy -t "$TARGET" --var "catalog=${CATALOG}"
databricks bundle run -t "$TARGET" mdm_init
databricks bundle run -t "$TARGET" mdm
