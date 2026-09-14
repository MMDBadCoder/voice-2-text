#!/usr/bin/env bash
# Delete audio + transcripts past RETENTION_DAYS. Wire into cron:
#   0 3 * * *  /srv/scripts/retention-sweep.sh >> /var/log/majles-retention.log 2>&1
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && . ./.env && set +a
PY="${PYTHON:-$ROOT/.venv/bin/python}"; [[ -x "$PY" ]] || PY="$(command -v python3)"
"$PY" -c "from app.tasks import sweep_retention; print(sweep_retention())"
