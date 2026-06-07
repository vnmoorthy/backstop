#!/usr/bin/env bash
# Backstop ⇄ Moss live retrieval service.
# Moss's SDK needs Python >=3.10; this sets up a 3.10+ venv, installs `moss`,
# builds the real index, and serves genuine semantic queries on :8021.
#   ./scripts/run_moss.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

VENV="${MOSS_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/backstop/mossenv}"
mkdir -p "$(dirname "$VENV")"
PY="$(command -v python3.11 || command -v python3.12 || command -v python3.13 || true)"
if [ -z "$PY" ]; then
  echo "ERROR: need Python 3.10+ for the Moss SDK (none found)." >&2; exit 1
fi

[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/python" -c "import moss" >/dev/null 2>&1 || "$VENV/bin/pip" install -q moss

# load ONLY the Moss keys from .env via a safe key=value reader — never `source`
# the file (sourcing executes it, so any $(...)/backtick in a value would run).
_envget() { [ -f ./.env ] && grep -E "^$1=" ./.env | tail -1 | cut -d= -f2- || true; }
export MOSS_PROJECT_ID="${MOSS_PROJECT_ID:-$(_envget MOSS_PROJECT_ID)}"
export MOSS_PROJECT_KEY="${MOSS_PROJECT_KEY:-$(_envget MOSS_PROJECT_KEY)}"
export MOSS_INDEX="${MOSS_INDEX:-$(_envget MOSS_INDEX)}"
if [ -z "${MOSS_PROJECT_ID:-}" ] || [ -z "${MOSS_PROJECT_KEY:-}" ]; then
  echo "ERROR: MOSS_PROJECT_ID / MOSS_PROJECT_KEY missing in .env" >&2; exit 1
fi

echo "▸ Moss retrieval service starting on :8021 (real index '${MOSS_INDEX:-backstop-appeals-rebuttals}')…"
exec "$VENV/bin/python" scripts/moss_service.py
