#!/usr/bin/env bash
# Serve over HTTPS with a self-signed certificate.
#
# Browsers expose the microphone only in a "secure context": HTTPS, or
# localhost. That is enforced inside the browser, so no server setting can
# unlock it over plain http://<ip>. A self-signed certificate satisfies it --
# the browser warns once about the unknown issuer, you accept, and the
# microphone works normally from then on.
#
# For a real deployment use a proper certificate (Let's Encrypt or your own CA)
# behind nginx/Caddy and set AUTH_COOKIE_SECURE=true.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && . ./.env && set +a
PY="${PYTHON:-$ROOT/.venv/bin/python}"; [[ -x "$PY" ]] || PY="$(command -v python3)"

CERT="${TLS_CERT:-$ROOT/certs/dev-cert.pem}"
KEY="${TLS_KEY:-$ROOT/certs/dev-key.pem}"
[[ -f "$CERT" && -f "$KEY" ]] || { echo "certificate missing: $CERT" >&2; exit 1; }

exec "$PY" -m uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" --port "${TLS_PORT:-8443}" \
  --ssl-certfile "$CERT" --ssl-keyfile "$KEY" --proxy-headers
