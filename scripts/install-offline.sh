#!/usr/bin/env bash
# Run this ON THE OFFLINE BOX, from the project root, with ./bundle present.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${BUNDLE_DIR:-$ROOT/bundle}"
[[ -d "$BUNDLE" ]] || { echo "bundle/ not found at $BUNDLE" >&2; exit 1; }

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

say "Installing Python packages from vendored wheels (no network)"
REQ="$BUNDLE/requirements.lock.txt"; [[ -f "$REQ" ]] || REQ="$ROOT/requirements.txt"
"${PYTHON_BIN:-python3.11}" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q --no-index --find-links="$BUNDLE/wheels" --upgrade pip || true
"$ROOT/.venv/bin/pip" install --no-index --find-links="$BUNDLE/wheels" -r "$REQ"

cp "$BUNDLE/requirements.lock.txt" "$ROOT/requirements.lock.txt" 2>/dev/null || true

say "Installing models"
mkdir -p "$ROOT/models"
cp -rn "$BUNDLE/models/." "$ROOT/models/" 2>/dev/null || true
du -sh "$ROOT/models"/* 2>/dev/null | sed 's/^/    /'

say "Installing fonts"
mkdir -p "$ROOT/app/static/fonts"
cp -n "$BUNDLE"/fonts/OFL.txt "$ROOT/app/static/fonts/" 2>/dev/null || true
cp -n "$BUNDLE"/fonts/*.woff2 "$ROOT/app/static/fonts/" 2>/dev/null || echo "    (none bundled)"

if [[ -d "$BUNDLE/images" ]] && command -v docker >/dev/null; then
  say "Loading docker images"
  for tar in "$BUNDLE"/images/*.tar; do [[ -f "$tar" ]] && docker load -i "$tar"; done
fi

[[ -f "$ROOT/.env" ]] || { cp "$ROOT/.env.example" "$ROOT/.env"; say "Created .env from template -- review it before starting"; }

say "Done. Start with:  ./scripts/run-local.sh   (or: docker compose up -d --scale worker=\$WORKER_COUNT)"
