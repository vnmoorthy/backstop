#!/usr/bin/env bash
# Backstop — one-command launcher. Foolproof for the live demo:
#   ./run.sh
# Finds python, installs deps if missing, starts the server from the right
# directory, and opens the dashboard. Ctrl-C to stop.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/orchestrator"

# pick a python that exists
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "ERROR: no python found. Install Python 3.9+ and re-run." >&2
  exit 1
fi
echo "▸ python: $PY ($($PY --version 2>&1))"

# install deps only if missing (fast on repeat runs)
if ! "$PY" -c "import fastapi, torch, reportlab, websockets" >/dev/null 2>&1; then
  echo "▸ installing dependencies (one time)…"
  "$PY" -m pip install -q -r requirements.txt
fi

# sanity: PAVO weights present + router loads
"$PY" -c "from backstop.pavo import MaskedPAVORouter; r=MaskedPAVORouter(); assert r.n_params==85041" \
  && echo "▸ PAVO router OK (85,041 params)"

PORT="${PORT:-8000}"
echo "▸ Backstop → http://localhost:$PORT   (Ctrl-C to stop)"

# open the dashboard once the server is up
( sleep 3; (command -v open >/dev/null && open "http://localhost:$PORT") || \
           (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") || true ) &

exec "$PY" -m backstop.server
