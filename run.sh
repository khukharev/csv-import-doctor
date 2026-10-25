#!/usr/bin/env bash
set -euo pipefail

# Works with spaces and non-ASCII characters in the path.
# The user activates the virtual environment beforehand.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

exec python -m uvicorn app_final:app \
  --host "${APP_HOST:-127.0.0.1}" \
  --port "${APP_PORT:-8000}" \
  --no-access-log
