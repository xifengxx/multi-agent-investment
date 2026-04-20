#!/usr/bin/env bash
set -euo pipefail

echo "[railway] starting web + 2 workers"

PYTHONPATH="${PYTHONPATH:-src}" python -m app.main web &
PYTHONPATH="${PYTHONPATH:-src}" python -m app.main worker --worker-id=worker-1 &
PYTHONPATH="${PYTHONPATH:-src}" python -m app.main worker --worker-id=worker-2 &

wait

