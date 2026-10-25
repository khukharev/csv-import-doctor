#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

exec python -m uvicorn app_final:app \
  --host "${APP_HOST:-127.0.0.1}" \
  --port "${APP_PORT:-8000}" \
  --no-access-log
