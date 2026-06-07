#!/usr/bin/env bash
# Backstop ⇄ Moss live retrieval service.
# Moss's SDK needs Python >=3.10; this sets up a 3.10+ venv, installs `moss`,
# builds the real index, and serves genuine semantic queries on :8021.
#   ./scripts/run_moss.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

VENV="${MOSS_VENV:-/tmp/mossenv}"
PY="$(command -v python3.11 || command -v python3.12 || command -v python3.13 || true)"
if [ -z "$PY" ]; then
  echo "ERROR: need Python 3.10+ for the Moss SDK (none found)." >&2; exit 1
fi

[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/python" -c "import moss" >/dev/null 2>&1 || "$VENV/bin/pip" install -q moss

# load the Moss keys from .env
set -a; . ./.env 2>/dev/null; set +a
if [ -z "${MOSS_PROJECT_ID:-}" ] || [ -z "${MOSS_PROJECT_KEY:-}" ]; then
  echo "ERROR: MOSS_PROJECT_ID / MOSS_PROJECT_KEY missing in .env" >&2; exit 1
fi

echo "▸ Moss retrieval service starting on :8021 (real index '${MOSS_INDEX:-backstop-appeals-rebuttals}')…"
exec "$VENV/bin/python" scripts/moss_service.py
