#!/usr/bin/env bash
# Run API + N workers without docker (systemd-free dev/small deployments).
# Reads the same .env as docker-compose, so the pool is tuned in one place.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && . ./.env && set +a

PY="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

WORKER_COUNT="${WORKER_COUNT:-2}"
PORT="${PORT:-8000}"
pids=()

cleanup() { echo; echo "stopping…"; kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "starting $WORKER_COUNT worker(s) x ${WORKER_CPU_THREADS:-4} thread(s)"
for i in $(seq 1 "$WORKER_COUNT"); do
  "$PY" -m app.worker & pids+=($!)
  echo "  worker $i -> pid ${pids[-1]}"
done

if [[ -n "${BALE_BOT_TOKEN:-}" ]]; then
  "$PY" -m app.bale & pids+=($!)
  echo "  Bale verification service -> pid ${pids[-1]}"
fi

echo "starting api on http://127.0.0.1:$PORT"
"$PY" -m uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "$PORT" & pids+=($!)
wait
