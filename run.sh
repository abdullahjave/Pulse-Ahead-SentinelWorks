#!/usr/bin/env bash
# Start SentinelWorks: builds the frontend if needed, computes validation if missing, serves on :8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d frontend/dist ]; then
  (cd frontend && npm install && npm run build)
fi
cd backend
if [ ! -f data/validation_results.json ]; then
  echo "Computing validation results (about 2 minutes)…"
  python3 -m sentinel.validation
fi
echo "SentinelWorks running at http://localhost:8000"
exec python3 -m uvicorn sentinel.api:app --host 0.0.0.0 --port 8000
